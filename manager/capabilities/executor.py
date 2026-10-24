"""能力执行：参数校验、同步/异步派发、超时、审计与结果截断。"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Mapping

from manager.capabilities.artifacts import ArtifactStore
from manager.capabilities.audit import AuditLog
from manager.capabilities.registry import CapabilityRegistry
from manager.shared.config import BrainConfig
from manager.shared.types import (
    Capability,
    CapabilityCall,
    CapabilityErrorCode,
    CapabilityResult,
)

logger = logging.getLogger(__name__)

AsyncCallback = Callable[[CapabilityCall, CapabilityResult], None]


class CapabilityExecutor:
    def __init__(
        self,
        registry: CapabilityRegistry,
        config: BrainConfig,
        *,
        artifacts: ArtifactStore | None = None,
        audit: AuditLog | None = None,
        on_async_result: AsyncCallback | None = None,
        inflight_path: str | None = None,
    ) -> None:
        self.registry = registry
        self.config = config
        self.artifacts = artifacts
        self.audit = audit
        self.on_async_result = on_async_result
        self.inflight_path = inflight_path
        self._inflight: dict[str, dict[str, Any]] = {}
        self._inflight_lock = threading.RLock()
        self._orphans: list[dict[str, Any]] | None = None
        self._closed = False
        self._pool = ThreadPoolExecutor(
            max_workers=max(1, config.max_concurrent_capabilities),
            thread_name_prefix="capability",
        )

    # ------------------------------------------------------------------
    def submit(
        self, call: CapabilityCall, *, caller: str = ""
    ) -> CapabilityResult | None:
        """执行一次调用。同步能力返回结果；异步能力返回 ``None``。"""
        capability = self.registry.get(call.capability)
        if capability is None:
            result = CapabilityResult.failure(
                CapabilityErrorCode.NOT_FOUND,
                f"未知能力: {call.capability}",
            )
            self._audit(call, result, 0.0, caller, record=bool(call.capability))
            return result

        errors = validate_args(capability.schema, call.args)
        if errors:
            result = CapabilityResult.failure(
                CapabilityErrorCode.INVALID_ARGS,
                "；".join(errors[:5]),
                {"capability": capability.name},
            )
            self._audit(call, result, 0.0, caller, record=capability.audit)
            return self._finalize(capability, result)

        started = time.monotonic()
        if capability.async_mode:
            self._track_inflight(call, caller)
            pending = _Pending(call, capability, caller, started, self)
            future = self._pool.submit(self._invoke, capability, call.args)
            future.add_done_callback(
                lambda completed, entry=pending: self._finish_async(entry, completed)
            )
            timer = threading.Timer(
                capability.timeout_seconds,
                self._timeout_async,
                args=(pending,),
            )
            timer.daemon = True
            pending.timer = timer
            timer.start()
            return None

        result = self._invoke(capability, call.args)
        self._audit(call, result, (time.monotonic() - started) * 1000, caller,
                    record=capability.audit)
        return self._finalize(capability, result)

    def submit_many(
        self, calls: list[CapabilityCall], *, caller: str = ""
    ) -> tuple[list[CapabilityResult], int]:
        """返回（同步结果, 异步派发数量）。"""
        results: list[CapabilityResult] = []
        pending = 0
        for call in calls:
            result = self.submit(call, caller=caller)
            if result is None:
                pending += 1
            else:
                results.append(result)
        return results, pending

    def close(self, timeout: float = 2.0) -> None:
        self._closed = True
        self._pool.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------------
    def take_orphans(self) -> list[dict[str, Any]]:
        """取走上次进程遗留的在途调用，并把结果标记为 unknown。"""
        with self._inflight_lock:
            if self._orphans is None:
                self._orphans = self._load_inflight()
                self._inflight = {}
                self._save_inflight()
            orphans, self._orphans = self._orphans, []
            return orphans

    def inflight_count(self) -> int:
        """当前在途的异步能力数量；供 busy_seconds 汇总。"""
        with self._inflight_lock:
            return len(self._inflight)

    def _track_inflight(self, call: CapabilityCall, caller: str) -> None:
        if not self.inflight_path:
            return
        key = call.call_id or f"{call.capability}:{time.monotonic()}"
        with self._inflight_lock:
            self._inflight[key] = {
                "call_id": call.call_id,
                "capability": call.capability,
                "caller": caller,
                "started_at": time.time(),
            }
            self._save_inflight()

    def _untrack_inflight(self, call: CapabilityCall) -> None:
        if not self.inflight_path:
            return
        with self._inflight_lock:
            if call.call_id and call.call_id in self._inflight:
                self._inflight.pop(call.call_id, None)
                self._save_inflight()

    def _load_inflight(self) -> list[dict[str, Any]]:
        if not self.inflight_path or not os.path.exists(self.inflight_path):
            return []
        try:
            with open(self.inflight_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError) as exc:
            logger.warning("读取在途能力记录失败：%s", exc)
            return []
        items = data.get("calls") if isinstance(data, dict) else data
        return [item for item in (items or []) if isinstance(item, dict)]

    def _save_inflight(self) -> None:
        if not self.inflight_path:
            return
        payload = {"calls": list(self._inflight.values())}
        try:
            os.makedirs(
                os.path.dirname(self.inflight_path), exist_ok=True
            )
            temp = f"{self.inflight_path}.tmp"
            with open(temp, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
            os.replace(temp, self.inflight_path)
        except OSError as exc:
            logger.warning("保存在途能力记录失败：%s", exc)

    # ------------------------------------------------------------------
    def _invoke(
        self, capability: Capability, args: Mapping[str, Any]
    ) -> CapabilityResult:
        try:
            value = capability.handler(**dict(args))
        except TypeError as exc:
            return CapabilityResult.failure(
                CapabilityErrorCode.INVALID_ARGS, str(exc)
            )
        except FileNotFoundError as exc:
            return CapabilityResult.failure(
                CapabilityErrorCode.CAPABILITY_ERROR, str(exc)
            )
        except Exception as exc:  # 工具异常不能拖垮进程
            logger.warning("能力 %s 执行失败：%s", capability.name, exc)
            return CapabilityResult.failure(
                CapabilityErrorCode.CAPABILITY_ERROR,
                f"{type(exc).__name__}: {exc}",
            )
        if isinstance(value, CapabilityResult):
            return value
        return CapabilityResult.success(value)

    def _finish_async(self, pending: "_Pending", future: Future) -> None:
        if not pending.claim():
            return
        if pending.timer is not None:
            pending.timer.cancel()
        try:
            result = future.result()
        except Exception as exc:
            result = CapabilityResult.failure(
                CapabilityErrorCode.CAPABILITY_ERROR,
                f"{type(exc).__name__}: {exc}",
            )
        duration = (time.monotonic() - pending.started) * 1000
        self._audit(
            pending.call,
            result,
            duration,
            pending.caller,
            record=pending.capability.audit,
        )
        self._untrack_inflight(pending.call)
        self._deliver(pending.call, self._finalize(pending.capability, result))

    def _timeout_async(self, pending: "_Pending") -> None:
        if not pending.claim():
            return
        result = CapabilityResult.failure(
            CapabilityErrorCode.TIMEOUT,
            f"{pending.capability.name} 超过 {pending.capability.timeout_seconds:g} 秒",
        )
        self._audit(
            pending.call,
            result,
            (time.monotonic() - pending.started) * 1000,
            pending.caller,
            record=pending.capability.audit,
        )
        self._untrack_inflight(pending.call)
        self._deliver(pending.call, result)

    def _deliver(self, call: CapabilityCall, result: CapabilityResult) -> None:
        if self._closed or self.on_async_result is None:
            return
        try:
            self.on_async_result(call, result)
        except Exception:
            logger.exception("异步能力结果回调失败")

    def _audit(
        self,
        call: CapabilityCall,
        result: CapabilityResult,
        duration_ms: float,
        caller: str,
        *,
        record: bool,
    ) -> None:
        if not record or self.audit is None:
            return
        self.audit.record(
            call.capability,
            call.args,
            ok=result.ok,
            duration_ms=duration_ms,
            caller=caller or (call.call_id or ""),
        )

    # ------------------------------------------------------------------
    def _finalize(
        self, capability: Capability, result: CapabilityResult
    ) -> CapabilityResult:
        if not result.ok:
            return result
        try:
            encoded = json.dumps(result.result, ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError):
            result.result = str(result.result)
            encoded = result.result.encode("utf-8")
        limit = self.config.capability_max_result_bytes
        if len(encoded) <= limit:
            return result

        ref = None
        if self.artifacts is not None:
            kind = capability.name.split(".", 1)[0]
            ref = self.artifacts.save(kind, result.result)

        preview_limit = max(200, limit // 2)
        result.result = _preview(result.result, preview_limit, ref)
        result.result_ref = ref
        result.truncated = True
        return result


class _Pending:
    def __init__(
        self,
        call: CapabilityCall,
        capability: Capability,
        caller: str,
        started: float,
        executor: CapabilityExecutor,
    ) -> None:
        self.call = call
        self.capability = capability
        self.caller = caller
        self.started = started
        self.executor = executor
        self.timer: threading.Timer | None = None
        self._lock = threading.Lock()
        self._done = False

    def claim(self) -> bool:
        with self._lock:
            if self._done:
                return False
            self._done = True
            return True


# ----------------------------------------------------------------------
def validate_args(
    schema: Mapping[str, Any] | None, args: Mapping[str, Any] | None
) -> list[str]:
    if not schema:
        return []
    args = args or {}
    if not isinstance(args, Mapping):
        return ["参数必须是对象"]
    errors: list[str] = []
    _validate(schema, args, "", errors)
    return errors


def _validate(
    schema: Mapping[str, Any], value: Any, path: str, errors: list[str]
) -> None:
    expected = schema.get("type")
    if expected == "object":
        if not isinstance(value, Mapping):
            errors.append(f"{path or 'args'} 应为对象")
            return
        required = schema.get("required") or []
        for key in required:
            if key not in value:
                errors.append(f"缺少参数 {path + '.' if path else ''}{key}")
        properties = schema.get("properties") or {}
        for key, item in value.items():
            subschema = properties.get(key)
            if isinstance(subschema, Mapping):
                _validate(subschema, item, f"{path}.{key}" if path else key, errors)
        return

    if "oneOf" in schema:
        branches = schema.get("oneOf") or []
        for branch in branches:
            branch_errors: list[str] = []
            if isinstance(branch, Mapping):
                _validate(branch, value, path, branch_errors)
                if not branch_errors:
                    return
        errors.append(f"{path or 'args'} 不符合允许的类型")
        return

    if expected is not None and not _type_ok(expected, value):
        errors.append(f"{path or 'args'} 应为 {expected}")
        return

    enum = schema.get("enum")
    if enum is not None and value not in enum:
        errors.append(f"{path or 'args'} 必须是 {enum} 之一")
    if isinstance(value, str):
        min_length = schema.get("minLength")
        max_length = schema.get("maxLength")
        if min_length is not None and len(value) < int(min_length):
            errors.append(f"{path} 太短")
        if max_length is not None and len(value) > int(max_length):
            errors.append(f"{path} 太长")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if minimum is not None and value < minimum:
            errors.append(f"{path} 不能小于 {minimum}")
        if maximum is not None and value > maximum:
            errors.append(f"{path} 不能大于 {maximum}")
    if expected == "array" and isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for index, item in enumerate(value):
                _validate(item_schema, item, f"{path}[{index}]", errors)


def _type_ok(expected: str, value: Any) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "array":
        return isinstance(value, (list, tuple))
    if expected == "object":
        return isinstance(value, Mapping)
    if expected == "null":
        return value is None
    return True


def _preview(data: Any, limit: int, ref: str | None) -> str:
    if isinstance(data, str):
        text = data
    else:
        try:
            text = json.dumps(data, ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(data)
    encoded = text.encode("utf-8")
    if len(encoded) > limit:
        text = encoded[:limit].decode("utf-8", errors="ignore")
    suffix = "…"
    if ref:
        suffix = f"…（完整内容见 {ref}）"
    return text + suffix


__all__ = ["CapabilityExecutor", "validate_args"]
