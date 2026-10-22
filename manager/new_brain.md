# Alice 兔兔桌宠架构

> Alice 是一只生活在 PC 里的兔兔。一个 LLM 是她的决策中心，根据当前的内部因素与外部因素决定她下一步做什么。
> 本文从**全局**到**局部**，描述 Alice 桌宠的架构。

## 1. Alice 的定位

Alice 的目的是构建一个持续存在的智能生物，有身体状态、情绪等内因，能感知外部时间、事件等外因，并做出相应的决策。

## 2. 模块划分

- brain.py  Alice 的中枢，负责装配所有子模块、管理线程与生命周期
- body/  身体模块，记录 Alice 当前的各项身体数值与状态，并派生情绪、态度、欲望
- environment/  环境模块，获取 Alice 周围的环境信息（电脑上的环境信息）
- memory/  记忆模块，存储 Alice 的长短期记忆，并承担定期蒸馏记忆的功能
- capabilities/  能力模块，管理 Alice 当前所有可以执行的操作
- activity/  活动模块，管理当前 Alice 还没有完成的活动的状态
- lifecycle/  生命循环，低频 tick（1~5 秒）推进身体与环境信息变化，把闹钟、显著变化、休息结束等唤醒事件封装进 event_queue
- event_queue/  事件队列，存储任何可以触发 Alice 思考并决策的事件，并负责事件的持久化与崩溃恢复
- frame_generator/  帧生产者，取出当前一个或多个唤醒事件，与身体、环境、记忆、能力、活动信息聚合，整理成一帧数据传给 thinker
- thinker/  认知决策，调用 LLM 读帧数据，产出并校验 Decision
- executor/  执行器，接收 Decision 并落地

## 3. 模块交互接口

| 模块 | 接口 | 作用 |
| --- | --- | --- |
| brain.py | `start()` | 装配并启动所有子模块与线程 |
| | `stop(reason)` | 停止线程、保存状态 |
| | `tick(delta)` | 推进状态时钟（轻量，不调 LLM）|
| | `submit_event(event)` | 外部事件入队并触发唤醒 |
| | `snapshot()` | 返回桌宠只读快照 |
| body/ | `tick(perception, interactions, dt)` | 按时间与互动推进身体数值 |
| | `set_mode(mode)` | 切换醒 / 休息 |
| | `stimulate(amount)` | 喂食/互动刺激警觉 |
| | `snapshot()` | 返回身体快照 |
| | `derive(body, memory_view, episodes, now)` | 派生情绪 / 态度 / 欲望 |
| environment/ | `tick(bunny_snapshot, qq_connected)` | 聚合一次环境快照 |
| memory/ | `view(speaker)` | 取记忆只读视图（自我/目标/偏好/说话人/索引）|
| | `recall(query, limit)` | 关键词召回相关记忆 |
| | `read_semantic(target)` | 读某份语义记忆 |
| | `write_semantic(target, content)` | 写语义记忆 |
| | `record_turn(speaker, user, reply, at)` | 记一轮对话进 L1 |
| | `recent_episodes(window, limit)` | 取近期情景记忆 |
| capabilities/ | `names()` | 能力名列表（给情境）|
| | `as_dicts()` | 能力 schema 清单（给 prompt）|
| | `submit(call)` | 执行一次能力调用 |
| | `submit_many(calls)` | 并发执行多个能力调用 |
| activity/ | `start(kind, goal)` | 开始一段活动 |
| | `pause()` / `resume()` | 暂停 / 恢复活动 |
| | `complete()` / `abandon()` | 完成 / 放弃活动 |
| | `active()` / `paused()` | 取当前活动 |
| | `update(...)` | 更新活动进度 / 状态 |
| lifecycle/ | `tick(delta)` | 推进身体/环境、检测显著变化 |
| | `schedule(next_wake_in, wake_when)` | 排下一次唤醒 |
| | `due()` | 返回到期唤醒理由（闹钟/信号/最长发呆/休息）|
| event_queue/ | `add(event)` | 事件入队 |
| | `unhandled()` | 取未处理事件 |
| | `mark_handled(ids)` | 标记事件已处理 |
| | `load()` | 启动时恢复账本 |
| | `all()` | 取全部事件（账本）|
| frame_generator/ | `build(唤醒理由, 只读视图)` | 聚合成一帧（情境 + prompt）|
| thinker/ | `run(一帧)` | 调 LLM 产出并校验 Decision |
| executor/ | `apply(decision)` | 落地 Decision（发消息/能力/活动/记忆/身体/闹钟）|