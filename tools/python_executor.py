import os
import shutil
import subprocess
import sys
import tempfile

_EXEC_TIMEOUT = 10

# 子进程里先执行的前导代码：
# 1) 把工作目录锁进临时目录
# 2) 拦截对临时目录之外任何路径的读写（open / os.open / os 文件操作 / shutil）
# 3) 封死子进程派生（防止 pip install / 逃逸出沙箱）
_SANDBOX_PRELUDE = r'''
import os, sys, builtins, io, shutil, subprocess

sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

_SBX = os.path.realpath(sys.argv[1])
os.chdir(_SBX)

def _in_sbx(path):
    p = os.path.realpath(os.path.abspath(os.fspath(path)))
    return p == _SBX or p.startswith(_SBX + os.sep)

def _guard(path):
    if not _in_sbx(path):
        raise PermissionError(f"sandbox 禁止访问 tmp 之外的路径: {path}")

def _blocked(name):
    def _f(*args, **kwargs):
        raise PermissionError(f"sandbox 禁止 {name}")
    return _f

# open / io.open（pathlib 内部走 io.open，一并拦住）
_real_open = builtins.open
def _safe_open(file, *a, **k):
    _guard(file)
    return _real_open(file, *a, **k)
builtins.open = _safe_open
io.open = _safe_open

# os.open 低层文件描述符
if hasattr(os, "open"):
    _real_os_open = os.open
    def _safe_os_open(path, *a, **k):
        _guard(path)
        return _real_os_open(path, *a, **k)
    os.open = _safe_os_open

# os 里会改文件系统 / 工作目录的函数
_MUT = ("remove", "unlink", "rename", "renames", "replace", "rmdir",
        "mkdir", "makedirs", "chdir", "chmod", "symlink", "link", "utime")
for _n in _MUT:
    if hasattr(os, _n):
        _f = getattr(os, _n)
        def _make(_f=_f):
            def _w(path, *a, **k):
                _guard(path)
                return _f(path, *a, **k)
            return _w
        setattr(os, _n, _make())

# shutil 的搬运/删除直接封死（可能把 tmp 外内容读进来或写出去）
for _n in ("copy", "copy2", "copyfile", "copytree", "move", "rmtree", "chown"):
    if hasattr(shutil, _n):
        setattr(shutil, _n, _blocked(_n))

# 禁止派生进程（pip / cmd / 其它程序）
for _n in ("Popen", "run", "call", "check_call", "check_output",
           "getoutput", "getstatusoutput"):
    if hasattr(subprocess, _n):
        setattr(subprocess, _n, _blocked(f"subprocess.{_n}"))

for _n in ("system", "popen", "startfile",
           "spawnl", "spawnle", "spawnlp", "spawnlpe",
           "spawnv", "spawnve", "spawnvp", "spawnvpe",
           "execl", "execle", "execlp", "execlpe",
           "execv", "execve", "execvp", "execvpe"):
    if hasattr(os, _n):
        setattr(os, _n, _blocked(f"os.{_n}"))

exec(compile(sys.stdin.read(), "<sandbox>", "exec"), {"__name__": "__main__"})
'''


def execute_python(code: str) -> str:
    """在沙箱里执行一段 Python 代码，返回其标准输出 / 错误输出。

    沙箱：子进程工作目录为一个临时目录，只能读写该临时目录内的文件，
    禁止派生子进程（不能 pip / 逃逸），最长运行 10 秒，结束即删除临时目录。
    """
    if getattr(sys, "frozen", False):
        exe = shutil.which("python") or shutil.which("python3")
        if not exe:
            return "当前为打包运行且系统未安装 Python，无法执行代码"
        cmd = [exe]
    else:
        cmd = [sys.executable]

    sbx = tempfile.mkdtemp(prefix="bunny_sandbox_")
    try:
        # 最小化环境变量：既不给子进程继承项目密钥，也把临时目录指进沙箱
        env = {
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", r"C:\Windows"),
            "PATH": os.environ.get("PATH", ""),
            "TEMP": sbx,
            "TMP": sbx,
        }
        result = subprocess.run(
            cmd + ["-I", "-c", _SANDBOX_PRELUDE, sbx],
            input=code,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_EXEC_TIMEOUT,
            cwd=sbx,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return f"执行超时（超过 {_EXEC_TIMEOUT} 秒）"
    except Exception as e:
        return f"执行失败：{e}"
    finally:
        shutil.rmtree(sbx, ignore_errors=True)

    out = (result.stdout or "").strip()
    err = (result.stderr or "").strip()
    parts = [p for p in (out, err) if p]
    return "\n".join(parts) if parts else "（执行完成，无输出）"
