"""The wall around the card worker (docs/v2 page 6): once its code is loaded, the worker locks itself with the kernel's
seccomp filter, so a card can reach nothing outside the card layer on its own: no file is opened, made, changed or
removed, no network socket is made, no program started, no other process signalled or read. What it already holds
stays usable: the channel to the core (its stdin and stdout), its threads, its memory. Everything outside goes over
that channel, through the core's interface, which checks it.

Home Assistant OS has no Landlock; seccomp it has. The filter only says no (EPERM) to the calls below, every other
call goes on as before; it cannot be lifted again, and a child (none can be made) would inherit it.
"""

from __future__ import annotations

import ctypes
import errno
import os
import platform
import struct

PR_SET_NO_NEW_PRIVS, PR_SET_SECCOMP, SECCOMP_MODE_FILTER = 38, 22, 2
ALLOW, KILL = 0x7FFF0000, 0x80000000  # SECCOMP_RET_ALLOW, SECCOMP_RET_KILL_PROCESS


def _errno(number: int) -> int:
    return 0x00050000 | number  # SECCOMP_RET_ERRNO


# x86_64 and aarch64 (most machines Home Assistant runs on), without anything installed: the audit arch, and the calls
# by number (any other machine: libseccomp, by name). Files: opening,
# making, changing, removing; network: sockets; programs: exec, fork (threads stay: clone with CLONE_THREAD);
# other processes: signals, tracing, their memory; and what goes around a filter (io_uring, handles, bpf, namespaces).
_X86_64 = {"arch": 0xC000003E, "clone": 56, "clone3": 435, "kill": 62, "tkill": 200, "tgkill": 234, "deny": {
    "open": 2, "openat": 257, "openat2": 437, "creat": 85, "open_by_handle_at": 304, "name_to_handle_at": 303,
    "socket": 41, "socketpair": 53, "connect": 42, "bind": 49, "listen": 50, "accept": 43, "accept4": 288,
    "execve": 59, "execveat": 322, "fork": 57, "vfork": 58,
    "ptrace": 101, "process_vm_readv": 310, "process_vm_writev": 311, "pidfd_open": 434, "pidfd_send_signal": 424,
    "pidfd_getfd": 438, "process_madvise": 440,
    "unlink": 87, "unlinkat": 263, "rename": 82, "renameat": 264, "renameat2": 316, "mkdir": 83, "mkdirat": 258,
    "rmdir": 84, "link": 86, "linkat": 265, "symlink": 88, "symlinkat": 266, "chmod": 90, "fchmodat": 268,
    "fchmodat2": 452, "chown": 92, "lchown": 94, "fchownat": 260, "truncate": 76, "mknod": 133, "mknodat": 259,
    "utime": 132, "utimes": 235, "utimensat": 280, "futimesat": 261, "setxattr": 188, "lsetxattr": 189,
    "removexattr": 197, "lremovexattr": 198,
    "io_uring_setup": 425, "io_uring_enter": 426, "io_uring_register": 427, "bpf": 321, "perf_event_open": 298,
    "userfaultfd": 323, "mount": 165, "umount2": 166, "pivot_root": 155, "chroot": 161, "setns": 308, "unshare": 272,
    "keyctl": 250, "add_key": 248, "request_key": 249, "init_module": 175, "finit_module": 313, "delete_module": 176,
    "kexec_load": 246, "reboot": 169, "swapon": 167, "swapoff": 168, "fanotify_init": 300, "open_tree": 428,
    "move_mount": 429, "fsopen": 430, "fsmount": 432, "mount_setattr": 442}}
_AARCH64 = {"arch": 0xC00000B7, "clone": 220, "clone3": 435, "kill": 129, "tkill": 130, "tgkill": 131, "deny": {
    "openat": 56, "openat2": 437, "open_by_handle_at": 265, "name_to_handle_at": 264,
    "socket": 198, "socketpair": 199, "connect": 203, "bind": 200, "listen": 201, "accept": 202, "accept4": 242,
    "execve": 221, "execveat": 281,
    "ptrace": 117, "process_vm_readv": 270, "process_vm_writev": 271, "pidfd_open": 434, "pidfd_send_signal": 424,
    "pidfd_getfd": 438, "process_madvise": 440,
    "unlinkat": 35, "renameat": 38, "renameat2": 276, "mkdirat": 34, "linkat": 37, "symlinkat": 36, "fchmodat": 53,
    "fchmodat2": 452, "fchownat": 54, "truncate": 45, "mknodat": 33, "utimensat": 88, "setxattr": 5, "lsetxattr": 6,
    "removexattr": 14, "lremovexattr": 15,
    "io_uring_setup": 425, "io_uring_enter": 426, "io_uring_register": 427, "bpf": 280, "perf_event_open": 241,
    "userfaultfd": 282, "mount": 40, "umount2": 39, "pivot_root": 41, "chroot": 51, "setns": 268, "unshare": 97,
    "keyctl": 219, "add_key": 217, "request_key": 218, "init_module": 105, "finit_module": 273, "delete_module": 106,
    "kexec_load": 104, "reboot": 142, "swapon": 224, "swapoff": 225, "fanotify_init": 262, "open_tree": 428,
    "move_mount": 429, "fsopen": 430, "fsmount": 432, "mount_setattr": 442}}
CALLS = {"x86_64": _X86_64, "aarch64": _AARCH64}
CLONE_THREAD = 0x00010000
X32 = 0x40000000  # x86_64's x32 calls: another numbering, said no to as a whole

# classic BPF over struct seccomp_data {int nr; u32 arch; u64 ip; u64 args[6]}
_LD, _JEQ, _JSET, _JGE, _RET = 0x20, 0x15, 0x45, 0x35, 0x06
_NR, _ARCH, _ARG0 = 0, 4, 16


def _program(calls: dict, pid: int) -> list[tuple[int, int, int, int]]:
    """The filter: wrong arch → kill; the denied calls → EPERM; clone only for a thread; clone3 → ENOSYS (the C
    library falls back to clone, whose flags can be read); signals only to itself."""
    out: list[tuple[int, int, int, int]] = [(_LD, 0, 0, _ARCH), (_JEQ, 1, 0, calls["arch"]), (_RET, 0, 0, KILL),
                                             (_LD, 0, 0, _NR)]
    if calls is _X86_64:
        out += [(_JGE, 0, 1, X32), (_RET, 0, 0, _errno(errno.EPERM))]
    for nr in sorted(set(calls["deny"].values())):
        out += [(_JEQ, 0, 1, nr), (_RET, 0, 0, _errno(errno.EPERM))]
    out += [(_JEQ, 0, 1, calls["clone3"]), (_RET, 0, 0, _errno(errno.ENOSYS))]
    out += [(_JEQ, 0, 4, calls["clone"]), (_LD, 0, 0, _ARG0), (_JSET, 1, 0, CLONE_THREAD),
            (_RET, 0, 0, _errno(errno.EPERM)), (_RET, 0, 0, ALLOW)]
    for name in ("kill", "tgkill"):  # its own pid only (kill 0 or -1 would reach Home Assistant's processes)
        out += [(_JEQ, 0, 4, calls[name]), (_LD, 0, 0, _ARG0), (_JEQ, 1, 0, pid), (_RET, 0, 0, _errno(errno.EPERM)),
                (_RET, 0, 0, ALLOW), (_LD, 0, 0, _NR)]
    out += [(_JEQ, 0, 1, calls["tkill"]), (_RET, 0, 0, _errno(errno.EPERM))]
    out.append((_RET, 0, 0, ALLOW))
    return out


def lock() -> str:
    """Lock this process (for good); returns how (its own filter, or libseccomp's on another machine). Raises OSError
    when it cannot (no seccomp on this system, the kernel refuses): then it is not walled, the caller says so."""
    if platform.system() != "Linux":
        raise OSError(f"no seccomp on {platform.system()}")
    calls = CALLS.get(platform.machine())
    if calls is None:
        _lock_libseccomp()
        return f"libseccomp ({platform.machine()})"
    _lock_own(calls)
    return f"own filter ({platform.machine()})"


def _lock_own(calls: dict) -> None:
    program = _program(calls, os.getpid())
    blob = b"".join(struct.pack("HBBI", *ins) for ins in program)
    buffer = ctypes.create_string_buffer(blob, len(blob))

    class Prog(ctypes.Structure):
        _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.c_void_p)]

    libc = ctypes.CDLL(None, use_errno=True)
    prctl = libc.prctl
    prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
    if prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "no_new_privs refused")
    prog = Prog(len(program), ctypes.addressof(buffer))
    if prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, ctypes.addressof(prog), 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "seccomp refused")


class _Cmp(ctypes.Structure):
    _fields_ = [("arg", ctypes.c_uint), ("op", ctypes.c_int), ("a", ctypes.c_uint64), ("b", ctypes.c_uint64)]


def _lock_libseccomp() -> None:
    """Any other machine Linux runs on: libseccomp knows its numbers (by the same names), when it is there."""
    try:
        lib = ctypes.CDLL("libseccomp.so.2", use_errno=True)
    except OSError as err:
        raise OSError(f"no seccomp numbers for {platform.machine()} and no libseccomp: {err}") from err
    lib.seccomp_init.restype = ctypes.c_void_p
    lib.seccomp_init.argtypes = [ctypes.c_uint32]
    lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    lib.seccomp_rule_add_array.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint, ctypes.POINTER(_Cmp)]
    lib.seccomp_load.argtypes = [ctypes.c_void_p]
    lib.seccomp_release.argtypes = [ctypes.c_void_p]
    ctx = lib.seccomp_init(ALLOW)
    if not ctx:
        raise OSError("libseccomp: no filter")
    masked_eq, ne = 7, 1  # SCMP_CMP_MASKED_EQ, SCMP_CMP_NE

    def rule(name: str, action: int, *cmps: _Cmp) -> None:
        nr = lib.seccomp_syscall_resolve_name(name.encode())
        if nr < 0:  # this machine has no such call
            return
        array = (_Cmp * len(cmps))(*cmps) if cmps else None
        if lib.seccomp_rule_add_array(ctx, action, nr, len(cmps), array) != 0:
            raise OSError(f"libseccomp: no rule for {name}")

    try:
        for name in {*_X86_64["deny"], *_AARCH64["deny"], "socketcall", "ipc"}:  # socketcall: older 32-bit sockets
            rule(name, _errno(errno.EPERM))
        rule("clone3", _errno(errno.ENOSYS))
        rule("clone", _errno(errno.EPERM), _Cmp(0, masked_eq, CLONE_THREAD, 0))  # flags without CLONE_THREAD
        pid = os.getpid()
        rule("kill", _errno(errno.EPERM), _Cmp(0, ne, pid, 0))
        rule("tgkill", _errno(errno.EPERM), _Cmp(0, ne, pid, 0))
        rule("tkill", _errno(errno.EPERM))
        if lib.seccomp_load(ctx) != 0:
            raise OSError("libseccomp: the kernel refused the filter")
    finally:
        lib.seccomp_release(ctx)


def locked() -> bool:
    """This process has a seccomp filter."""
    try:
        status = open("/proc/self/status", encoding="ascii").read()  # noqa: SIM115 - fails once locked: that says it too
    except OSError:
        return True
    return any(line.startswith("Seccomp:") and line.split()[1] == "2" for line in status.splitlines())
