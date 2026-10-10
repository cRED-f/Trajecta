// Trajecta native Windows execution helper (no Docker, no external runtime).
// Build: cl /nologo /std:c++17 /EHsc /O2 native\\windows\\trajecta_sandbox.cpp
//        /Fe:trajecta-native-sandbox.exe userenv.lib advapi32.lib
// Trust boundary: Windows AppContainer + a Job Object. Fail CLOSED if either is unavailable.
// The Python adapter provisions an AppContainer-only ACE on the selected workspace.
#define UNICODE
#define _UNICODE
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <userenv.h>
#include <sddl.h>
#include <cstdio>
#include <cstdlib>
#include <cwchar>
#include <string>
#include <vector>
#include <stdexcept>
#include <algorithm>
#include <cstdint>

#pragma comment(lib, "userenv.lib")
#pragma comment(lib, "advapi32.lib")

struct Handle {
    HANDLE h = nullptr;
    explicit Handle(HANDLE v = nullptr) : h(v) {}
    ~Handle() { if (h && h != INVALID_HANDLE_VALUE) CloseHandle(h); }
    Handle(const Handle&) = delete;
    Handle& operator=(const Handle&) = delete;
};

static void fail(const char* what, DWORD code = GetLastError()) {
    fprintf(stderr, "Native sandbox setup failed: %s (Windows error %lu)\n", what, code);
    throw std::runtime_error(what);
}

static std::wstring arg(int argc, wchar_t** argv, const wchar_t* flag,
                        const wchar_t* fallback = L"") {
    for (int i = 1; i < argc - 1; ++i)
        if (wcscmp(argv[i], flag) == 0) return argv[i + 1];
    return fallback;
}

static bool has_arg(int argc, wchar_t** argv, const wchar_t* flag) {
    for (int i = 1; i < argc; ++i) if (wcscmp(argv[i], flag) == 0) return true;
    return false;
}

static std::wstring quote(const std::wstring& v) {
    // CommandLineToArgvW-compatible quoting of ONE argument.
    std::wstring q = L"\"";
    unsigned slashes = 0;
    for (wchar_t c : v) {
        if (c == L'\\') { ++slashes; continue; }
        if (c == L'"') { q.append(slashes * 2 + 1, L'\\'); q += L'"'; }
        else { q.append(slashes, L'\\'); q += c; }
        slashes = 0;
    }
    q.append(slashes * 2, L'\\');
    return q + L"\"";
}

static PSID profile_sid(const std::wstring& name) {
    PSID sid = nullptr;
    HRESULT hr = CreateAppContainerProfile(name.c_str(), name.c_str(),
                                          L"Trajecta restricted command executor", nullptr, 0, &sid);
    if (hr == HRESULT_FROM_WIN32(ERROR_ALREADY_EXISTS))
        hr = DeriveAppContainerSidFromAppContainerName(name.c_str(), &sid);
    if (FAILED(hr) || !sid) fail("CreateAppContainerProfile", HRESULT_CODE(hr));
    return sid;  // FreeSid(sid) at end
}

static std::wstring sid_text(PSID sid) {
    LPWSTR out = nullptr;
    if (!ConvertSidToStringSidW(sid, &out)) fail("ConvertSidToStringSidW");
    std::wstring result(out);
    LocalFree(out);
    return result;
}

int wmain(int argc, wchar_t** argv) {
  try {
    const auto profile = arg(argc, argv, L"--profile");
    if (profile.empty() || profile.size() > 80 ||
        !std::all_of(profile.begin(), profile.end(), [](wchar_t c) {
            return (c >= L'a' && c <= L'z') || (c >= L'0' && c <= L'9') || c == L'_';
        })) fail("Invalid AppContainer profile", ERROR_INVALID_PARAMETER);
    PSID sid = profile_sid(profile);
    if (has_arg(argc, argv, L"--prepare")) {
        wprintf(L"%ls\n", sid_text(sid).c_str());
        FreeSid(sid);
        return 0;
    }
    if (!has_arg(argc, argv, L"--execute")) fail("Missing mode", ERROR_INVALID_PARAMETER);
    const auto workspace = arg(argc, argv, L"--workspace");
    const auto command = arg(argc, argv, L"--command");
    const auto timeout_str = arg(argc, argv, L"--timeout-ms", L"300000");
    const auto memory_str = arg(argc, argv, L"--memory-bytes", L"536870912");
    const auto cpu_str = arg(argc, argv, L"--cpu-cores", L"1.0");
    if (workspace.empty() || command.empty()) fail("Missing workspace/command", ERROR_INVALID_PARAMETER);
    DWORD attrs = GetFileAttributesW(workspace.c_str());
    if (attrs == INVALID_FILE_ATTRIBUTES || !(attrs & FILE_ATTRIBUTE_DIRECTORY))
        fail("Workspace unavailable", ERROR_PATH_NOT_FOUND);

    // Start with zero capabilities: no internetClient or privateNetworkClientServer.
    // Network policy changes require a different AppContainer profile and explicit permission.
    PSID internet_sid = nullptr;
    SID_AND_ATTRIBUTES internet_cap{};
    SECURITY_CAPABILITIES caps{};
    caps.AppContainerSid = sid;
    if (has_arg(argc, argv, L"--network")) {
        if (!ConvertStringSidToSidW(L"S-1-15-3-1", &internet_sid)) fail("Internet capability SID");
        internet_cap.Sid = internet_sid;
        internet_cap.Attributes = SE_GROUP_ENABLED;
        caps.Capabilities = &internet_cap;
        caps.CapabilityCount = 1;
    }

    SIZE_T size = 0;
    InitializeProcThreadAttributeList(nullptr, 1, 0, &size);
    if (!size) fail("InitializeProcThreadAttributeList size");
    std::vector<unsigned char> buffer(size);
    auto* list = reinterpret_cast<LPPROC_THREAD_ATTRIBUTE_LIST>(buffer.data());
    if (!InitializeProcThreadAttributeList(list, 1, 0, &size)) fail("InitializeProcThreadAttributeList");
    if (!UpdateProcThreadAttribute(list, 0, PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
                                   &caps, sizeof(caps), nullptr, nullptr))
        fail("UpdateProcThreadAttribute");

    Handle job(CreateJobObjectW(nullptr, nullptr));
    if (!job.h) fail("CreateJobObjectW");
    // Entire process tree is terminated when the last job handle closes.
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits{};
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE |
                                               JOB_OBJECT_LIMIT_JOB_MEMORY;
    unsigned long long mem = _wcstoui64(memory_str.c_str(), nullptr, 10);
    if (mem < 16 * 1024 * 1024 || mem > static_cast<unsigned long long>(SIZE_MAX))
        fail("Invalid memory limit", ERROR_INVALID_PARAMETER);
    limits.JobMemoryLimit = static_cast<SIZE_T>(mem);
    if (!SetInformationJobObject(job.h, JobObjectExtendedLimitInformation,
                                 &limits, sizeof(limits))) fail("Set memory limits");

    double cores = wcstod(cpu_str.c_str(), nullptr);
    SYSTEM_INFO info{};
    GetSystemInfo(&info);
    if (!(cores > 0) || info.dwNumberOfProcessors == 0) fail("Invalid CPU limit", ERROR_INVALID_PARAMETER);
    DWORD rate = static_cast<DWORD>(std::min(10000.0, std::max(1.0,
                         10000.0 * cores / static_cast<double>(info.dwNumberOfProcessors))));
    JOBOBJECT_CPU_RATE_CONTROL_INFORMATION cpu{};
    cpu.ControlFlags = JOB_OBJECT_CPU_RATE_CONTROL_ENABLE | JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP;
    cpu.CpuRate = rate;
    if (!SetInformationJobObject(job.h, JobObjectCpuRateControlInformation,
                                 &cpu, sizeof(cpu))) fail("Set CPU limits");

    wchar_t tmp_dir[MAX_PATH]{};
    if (!GetTempPathW(MAX_PATH, tmp_dir)) fail("GetTempPathW");
    wchar_t output_path[MAX_PATH]{};
    if (!GetTempFileNameW(tmp_dir, L"trj", 0, output_path)) fail("GetTempFileNameW");
    SECURITY_ATTRIBUTES sa{sizeof(sa), nullptr, TRUE};
    Handle output(CreateFileW(output_path, GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE,
                              &sa, CREATE_ALWAYS, FILE_ATTRIBUTE_TEMPORARY, nullptr));
    if (!output.h || output.h == INVALID_HANDLE_VALUE) fail("Open output file");

    wchar_t sysdir[MAX_PATH]{};
    if (!GetSystemDirectoryW(sysdir, MAX_PATH)) fail("GetSystemDirectoryW");
    const std::wstring executable = std::wstring(sysdir) + L"\\WindowsPowerShell\\v1.0\\powershell.exe";
    const std::wstring cmdline = quote(executable) +
        L" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command " + quote(command);
    std::vector<wchar_t> mutable_cmd(cmdline.begin(), cmdline.end());
    mutable_cmd.push_back(L'\0');
    // Do not leak Trajecta provider keys/tokens into model-generated commands.
    // Minimal Windows environment. User toolchains must be explicitly provisioned.
    wchar_t windir[MAX_PATH]{};
    if (!GetWindowsDirectoryW(windir, MAX_PATH)) fail("GetWindowsDirectoryW");
    std::wstring path = std::wstring(windir) + L"\\System32;" + windir +
                         L";" + windir + L"\\System32\\WindowsPowerShell\\v1.0";
    const auto trusted_tools = arg(argc, argv, L"--tool-path");
    // Paths are discovered from installed executables by the Python adapter,
    // never inherited from the parent process' environment.
    if (!trusted_tools.empty()) path += L";" + trusted_tools;
    std::wstring environment = L"SystemRoot=" + std::wstring(windir) + L'\0';
    environment += L"windir=" + std::wstring(windir) + L'\0';
    environment += L"PATH=" + path + L'\0';
    environment += L"PATHEXT=.COM;.EXE;.BAT;.CMD";
    environment.push_back(L'\0');
    environment += L"PYTHONUTF8=1";
    environment.push_back(L'\0');
    environment += L"PYTHONIOENCODING=utf-8";
    environment.push_back(L'\0');
    environment.push_back(L'\0');
    Handle nul(CreateFileW(L"NUL", GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE,
                           &sa, OPEN_EXISTING, 0, nullptr));
    if (!nul.h || nul.h == INVALID_HANDLE_VALUE) fail("Open NUL input");
    STARTUPINFOEXW startup{};
    startup.StartupInfo.cb = sizeof(startup);
    startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
    startup.StartupInfo.hStdOutput = output.h;
    startup.StartupInfo.hStdError = output.h;
    startup.StartupInfo.hStdInput = nul.h;
    startup.lpAttributeList = list;
    PROCESS_INFORMATION proc{};
    // CREATE_SUSPENDED guarantees limits are assigned before untrusted code executes.
    if (!CreateProcessW(executable.c_str(), mutable_cmd.data(), nullptr, nullptr, TRUE,
                        EXTENDED_STARTUPINFO_PRESENT | CREATE_SUSPENDED | CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT,
                        environment.data(), workspace.c_str(), &startup.StartupInfo, &proc))
        fail("CreateProcessW restricted AppContainer");
    Handle process(proc.hProcess), thread(proc.hThread);
    if (!AssignProcessToJobObject(job.h, process.h)) {
        TerminateProcess(process.h, 250);
        fail("AssignProcessToJobObject");
    }
    DeleteProcThreadAttributeList(list);
    if (ResumeThread(thread.h) == static_cast<DWORD>(-1)) {
        TerminateJobObject(job.h, 250);
        fail("ResumeThread");
    }

    unsigned long long requested = _wcstoui64(timeout_str.c_str(), nullptr, 10);
    DWORD timeout = static_cast<DWORD>(std::min(3600000ULL, std::max(1000ULL, requested)));
    DWORD wait = WaitForSingleObject(process.h, timeout);
    if (wait == WAIT_TIMEOUT) TerminateJobObject(job.h, 124);
    else if (wait != WAIT_OBJECT_0) { TerminateJobObject(job.h, 250); fail("WaitForSingleObject"); }
    DWORD status = 250;
    if (wait == WAIT_TIMEOUT) status = 124;
    else GetExitCodeProcess(process.h, &status);
    // Close inherited writer in parent, then read output. Cap output at 200 KB.
    CloseHandle(output.h);
    output.h = nullptr;
    Handle input(CreateFileW(output_path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE,
                             nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr));
    if (input.h && input.h != INVALID_HANDLE_VALUE) {
        char data[8192];
        DWORD count = 0;
        size_t total = 0;
        while (ReadFile(input.h, data, sizeof(data), &count, nullptr) && count) {
            const size_t room = total < 200000 ? 200000 - total : 0;
            fwrite(data, 1, std::min(static_cast<size_t>(count), room), stdout);
            total += count;
        }
        if (total > 200000) fputs("\n[Output truncated by sandbox]\n", stdout);
    }
    DeleteFileW(output_path);
    if (internet_sid) LocalFree(internet_sid);
    FreeSid(sid);
    return status == 124 ? 124 : static_cast<int>(status % 256);
  } catch (...) {
    return 250; // Fail closed. Never start an unrestricted host process.
  }
}
