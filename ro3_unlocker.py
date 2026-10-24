import ctypes
from ctypes import wintypes
import sys
import os
import time

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
ntdll = ctypes.WinDLL("ntdll")

# Win32 / NTSTATUS Constants
STATUS_SUCCESS = 0x00000000
STATUS_INFO_LENGTH_MISMATCH = 0xC0000004
SystemExtendedHandleInformation = 64
ObjectTypeInformation = 2
ObjectNameInformation = 1

PROCESS_DUP_HANDLE = 0x0040
PROCESS_QUERY_INFORMATION = 0x0400
DUPLICATE_CLOSE_SOURCE = 0x00000001
DUPLICATE_SAME_ACCESS = 0x00000002
TH32CS_SNAPPROCESS = 0x00000002


class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_char * 260),
    ]


class SYSTEM_HANDLE_TABLE_ENTRY_INFO_EX(ctypes.Structure):
    _fields_ = [
        ("Object", ctypes.c_void_p),
        ("UniqueProcessId", ctypes.c_size_t),
        ("HandleValue", ctypes.c_size_t),
        ("GrantedAccess", wintypes.ULONG),
        ("CreatorBackTraceIndex", wintypes.USHORT),
        ("ObjectTypeIndex", wintypes.USHORT),
        ("HandleAttributes", wintypes.ULONG),
        ("Reserved", wintypes.ULONG),
    ]


class UNICODE_STRING(ctypes.Structure):
    _fields_ = [
        ("Length", wintypes.USHORT),
        ("MaximumLength", wintypes.USHORT),
        ("Pad", wintypes.ULONG),
        ("Buffer", ctypes.c_void_p),
    ]


def get_process_ids_by_name(exe_name: str) -> list[int]:
    pids = []
    h_snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if h_snap == wintypes.HANDLE(-1).value or h_snap == -1:
        return pids

    pe = PROCESSENTRY32()
    pe.dwSize = ctypes.sizeof(PROCESSENTRY32)
    exe_target = exe_name.lower().encode("ascii")
    if not exe_target.endswith(b".exe"):
        exe_target += b".exe"

    if kernel32.Process32First(h_snap, ctypes.byref(pe)):
        while True:
            name = pe.szExeFile.lower()
            if name == exe_target:
                pids.append(pe.th32ProcessID)
            if not kernel32.Process32Next(h_snap, ctypes.byref(pe)):
                break

    kernel32.CloseHandle(h_snap)
    return pids


def close_ro3_mutex(target_pid: int) -> tuple[bool, int]:
    h_proc = kernel32.OpenProcess(
        PROCESS_DUP_HANDLE | PROCESS_QUERY_INFORMATION, False, target_pid
    )
    if not h_proc:
        return False, f"Failed to open process (error {kernel32.GetLastError()})"

    cur_proc = kernel32.GetCurrentProcess()
    try:
        size = wintypes.ULONG(16777216)  # 16 MB buffer
        buf = ctypes.create_string_buffer(size.value)
        status = ntdll.NtQuerySystemInformation(
            SystemExtendedHandleInformation, buf, size, ctypes.byref(size)
        )

        while (status & 0xFFFFFFFF) == STATUS_INFO_LENGTH_MISMATCH:
            size.value = int(size.value * 1.5)
            buf = ctypes.create_string_buffer(size.value)
            status = ntdll.NtQuerySystemInformation(
                SystemExtendedHandleInformation, buf, size, ctypes.byref(size)
            )

        if (status & 0xFFFFFFFF) != STATUS_SUCCESS:
            return False, f"NtQuerySystemInformation failed: 0x{status & 0xFFFFFFFF:08x}"

        num_handles = ctypes.cast(buf, ctypes.POINTER(ctypes.c_size_t)).contents.value
        entry_size = ctypes.sizeof(SYSTEM_HANDLE_TABLE_ENTRY_INFO_EX)
        offset = ctypes.sizeof(ctypes.c_size_t) * 2

        closed_count = 0

        for i in range(num_handles):
            entry_bytes = buf[offset + i * entry_size : offset + (i + 1) * entry_size]
            entry = SYSTEM_HANDLE_TABLE_ENTRY_INFO_EX.from_buffer_copy(entry_bytes)

            if entry.UniqueProcessId != target_pid:
                continue

            handle_val = entry.HandleValue
            h_dup = wintypes.HANDLE()

            res = kernel32.DuplicateHandle(
                h_proc,
                wintypes.HANDLE(handle_val),
                cur_proc,
                ctypes.byref(h_dup),
                0,
                False,
                DUPLICATE_SAME_ACCESS,
            )
            if not res:
                continue

            t_buf = ctypes.create_string_buffer(512)
            if ntdll.NtQueryObject(h_dup, ObjectTypeInformation, t_buf, 512, None) == 0:
                tu = UNICODE_STRING.from_buffer_copy(t_buf[:16])
                if tu.Buffer:
                    t_name = ctypes.wstring_at(tu.Buffer, tu.Length // 2)
                    if t_name == "Mutant":
                        n_buf = ctypes.create_string_buffer(1024)
                        if (
                            ntdll.NtQueryObject(
                                h_dup, ObjectNameInformation, n_buf, 1024, None
                            )
                            == 0
                        ):
                            nu = UNICODE_STRING.from_buffer_copy(n_buf[:16])
                            if nu.Buffer and nu.Length > 0:
                                obj_name = ctypes.wstring_at(nu.Buffer, nu.Length // 2)
                                if "RO3.GameClient.SingleInstance.v1" in obj_name:
                                    kernel32.CloseHandle(h_dup)
                                    dummy = wintypes.HANDLE()
                                    kernel32.DuplicateHandle(
                                        h_proc,
                                        wintypes.HANDLE(handle_val),
                                        0,
                                        ctypes.byref(dummy),
                                        0,
                                        False,
                                        DUPLICATE_CLOSE_SOURCE,
                                    )
                                    closed_count += 1
                                    print(
                                        f"[+] Mutex 'RO3.GameClient.SingleInstance.v1' fechado com sucesso no PID {target_pid} (Handle: 0x{handle_val:x})!"
                                    )
                                    continue

            kernel32.CloseHandle(h_dup)

        return True, closed_count
    finally:
        kernel32.CloseHandle(h_proc)


def run_loop():
    print("=" * 60)
    print("RO3 Multi-Client Unlocker - Monitoramento Ativo")
    print("Aguardando novas instâncias de 'ro3.exe'...")
    print("Pressione Ctrl+C para encerrar.")
    print("=" * 60)

    unlocked_pids = set()

    while True:
        try:
            current_pids = set(get_process_ids_by_name("ro3"))
            unlocked_pids = unlocked_pids.intersection(current_pids)

            for pid in current_pids:
                if pid not in unlocked_pids:
                    print(f"[*] Instância detectada: PID {pid}. Buscando Mutex...")
                    ok, count = close_ro3_mutex(pid)
                    if ok and count > 0:
                        unlocked_pids.add(pid)
                        print(
                            f"[OK] Cliente PID {pid} liberado! Você já pode abrir outra instância."
                        )
                    else:
                        time.sleep(0.5)

            time.sleep(1)
        except KeyboardInterrupt:
            print("\nMonitoramento encerrado.")
            break
        except Exception as e:
            print(f"Erro: {e}")
            time.sleep(2)


if __name__ == "__main__":
    run_loop()
