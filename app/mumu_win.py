from pathlib import Path
import json,os,re,shlex,subprocess,time

def run(exe, args, *, timeout=30):
    return subprocess.run(
        [str(exe), *map(str, args)], cwd=str(Path(exe).parent),
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )

def json_output(text):
    """Accept JSON after a vendor log prefix; do not evaluate its contents."""
    decoder = json.JSONDecoder()
    for match in re.finditer(r"[\[{]", text.lstrip("\ufeff")):
        try:
            return decoder.raw_decode(text.lstrip("\ufeff")[match.start():])[0]
        except ValueError:
            continue
    raise RuntimeError("MuMuManager did not return JSON: " + text[:300])

def objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from objects(child)

def resolve_manager(path):
    path = Path(os.path.expandvars(str(path).strip().strip('"')))
    for candidate in (path, path / "nx_main/MuMuManager.exe", path / "shell/MuMuManager.exe"):
        if candidate.is_file() and candidate.name.lower() == "mumumanager.exe":
            return candidate.resolve()
    return None

def find_manager(explicit=None):
    if explicit:
        found = resolve_manager(explicit)
        if not found:
            raise RuntimeError("MuMuManager.exe not found at: " + str(explicit))
        return found
    candidates = []
    if os.name == "nt":
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", 0, winreg.KEY_READ | view) as keys:
                        for i in range(winreg.QueryInfoKey(keys)[0]):
                            try:
                                with winreg.OpenKey(keys, winreg.EnumKey(keys, i)) as item:
                                    name = str(winreg.QueryValueEx(item, "DisplayName")[0])
                                    if "mumu" not in name.lower():
                                        continue
                                    try:
                                        candidates.append(str(winreg.QueryValueEx(item, "InstallLocation")[0]))
                                    except OSError:
                                        pass
                                    for key in ("UninstallString", "DisplayIcon"):
                                        try:
                                            raw = str(winreg.QueryValueEx(item, key)[0])
                                            match = re.match(r'^"([^"]+)"|^(.*?\.exe)', raw, re.I)
                                            if match:
                                                candidates.append(str(Path(match.group(1) or match.group(2)).parent))
                                        except OSError:
                                            pass
                            except OSError:
                                continue
                except OSError:
                    pass
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.environ.get("LOCALAPPDATA")):
        if base:
            for vendor in ("Netease", "NetEase", "Programs/NetEase", ""):
                for product in ("MuMu", "MuMuPlayer", "MuMuPlayerGlobal", "MuMuPlayer-12.0"):
                    candidates.append(str(Path(base) / vendor / product))
    for raw in dict.fromkeys(candidates):
        if raw:
            found = resolve_manager(raw)
            if found:
                return found
    return None

class Manager:
    def __init__(self, exe):
        self.exe = Path(exe)

    def call(self, *args):
        result = run(self.exe, args)
        text = result.stdout.strip()
        if result.returncode:
            raise RuntimeError("MuMuManager failed: " + (result.stderr or text)[:500])
        return text

    def instances(self):
        data = json_output(self.call("info", "-v", "all"))
        rows = {}
        for row in objects(data):
            if "index" in row and ("name" in row or "is_process_started" in row):
                rows[int(row["index"])] = row
        return rows

    def info(self, index):
        rows = self.instances()
        if index not in rows:
            raise RuntimeError("MuMu instance not found: " + str(index))
        return rows[index]

    def setting(self, index, key):
        data = json_output(self.call("setting", "-v", index, "-k", key))
        for row in objects(data):
            if key in row:
                return row[key]
        raise RuntimeError("This MuMu version does not expose setting " + key)

def truth(value):
    return value is True or str(value).lower() in ("true", "1")

def choose_instance(manager, requested=None):
    rows = manager.instances()
    if not rows:
        print("Creating the first MuMu Android instance...", flush=True)
        manager.call("create")
        rows = manager.instances()
    if not rows:
        raise RuntimeError("Create an Android device in MuMu, then run START.cmd again.")
    if requested is not None:
        if requested not in rows:
            raise RuntimeError("Requested MuMu instance does not exist: " + str(requested))
        return requested
    if len(rows) == 1:
        return next(iter(rows))
    print("Choose the MuMu instance to configure:")
    for index, row in sorted(rows.items()):
        print(f"  {index}: {row.get('name', 'Android')}")
    while True:
        value = input("Instance number: ").strip()
        if value.isdigit() and int(value) in rows:
            return int(value)

def enable_root(manager, index, work):
    try:
        before = manager.setting(index, "root_permission")
    except RuntimeError:
        print("Enable Root and LOCAL ADB in MuMu Device Settings > Developer/Other.")
        input("Restart that Android device and press Enter here: ")
        return
    if truth(before):
        return
    backup = work / f"root-setting-before-{index}.json"
    if not backup.exists():
        backup.write_text(json.dumps({"manager": str(manager.exe), "index": index, "root_permission": before}, indent=2))
    print(f"Enabling MuMu root for instance {index}; restarting this device only.", flush=True)
    if truth(manager.info(index).get("is_process_started", False)):
        manager.call("control", "-v", index, "shutdown")
        deadline = time.monotonic() + 60
        while truth(manager.info(index).get("is_process_started", False)):
            if time.monotonic() > deadline:
                raise RuntimeError("Close the selected Android device, then run START.cmd again.")
            time.sleep(2)
    manager.call("setting", "-v", index, "-k", "root_permission", "-val", "true")
    if not truth(manager.setting(index, "root_permission")):
        raise RuntimeError("Root setting was not applied; enable Root in MuMu settings and restart.")

def wait_android(manager, index, timeout=300):
    row = manager.info(index)
    if not truth(row.get("is_process_started", False)):
        manager.call("control", "-v", index, "launch")
    deadline = time.monotonic() + timeout
    print("Waiting for MuMu Android to finish booting...", flush=True)
    while time.monotonic() < deadline:
        row = manager.info(index)
        if row.get("launch_err_code"):
            raise RuntimeError("MuMu startup error: " + str(row.get("launch_err_msg", row["launch_err_code"])))
        if truth(row.get("is_android_started", False)) and row.get("adb_port"):
            return row
        time.sleep(3)
    raise RuntimeError("MuMu did not finish booting. Check its window, then run START.cmd again.")

def find_adb(manager_path):
    folder = Path(manager_path).parent
    root = folder.parent if folder.name.lower() in ("nx_main", "shell") else folder
    for rel in ("nx_main/adb.exe", "shell/adb.exe", "vmonitor/bin/adb_server.exe", "nx_device/12.0/shell/adb.exe", "nx_device/15.0/shell/adb.exe"):
        if (root / rel).is_file():
            return root / rel
    for name in ("adb.exe", "adb_server.exe"):
        for candidate in root.rglob(name):
            if candidate.is_file():
                return candidate
    raise RuntimeError("ADB not found in the selected MuMu installation: " + str(root))

def local_serial(row):
    host = str(row.get("adb_host_ip", "127.0.0.1"))
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise RuntimeError("Only local MuMu ADB is supported. Disable remote/bridged ADB.")
    port = int(row["adb_port"])
    if not 1 <= port <= 65535:
        raise RuntimeError("Invalid MuMu ADB port.")
    return f"127.0.0.1:{port}"
