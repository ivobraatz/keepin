# keepin

Pinterest pin backup and sync tool. Downloads all your saved pins and keeps them safe locally. Runs continuously in the background so new pins are downloaded as you save them.

Pins deleted from Pinterest stay in your local copy forever.

---

## How it works

keepin uses Pinterest's internal web API (the same one the browser uses) to fetch your saved pins and boards. It authenticates with a session cookie you copy from your browser. All data is stored locally — no third-party service involved.

State is tracked in a SQLite database so pins are never downloaded twice. Files are organized into folders named after your boards.

---

## Requirements

- Python 3.11 or newer
- pip

---

## Installation

```
git clone https://github.com/yourusername/keepin
cd keepin
pip install -e .
```

---

## Configuration

**Step 1 — create the config file:**

```
keepin init
```

This creates `keepin.toml` in the current directory.

**Step 2 — get your session cookie:**

1. Open [pinterest.com](https://www.pinterest.com) in your browser and log in
2. Open DevTools with F12
3. Go to Application -> Cookies -> https://www.pinterest.com
4. Find the cookie named `_pinterest_sess` and copy its value
5. Paste it into `session_cookie` in `keepin.toml`
6. Set `username` to your Pinterest handle (the part after `pinterest.com/` in your profile URL)

The session cookie expires after a few weeks. When keepin logs an authentication error, repeat step 2.

**Config file reference (`keepin.toml`):**

```toml
[pinterest]
session_cookie = "your_session_cookie_here"
username       = "your_username"

[storage]
output_dir  = "pins"      # relative to this config file, or absolute path
organize_by = "board"     # "board" = subfolder per board | "flat" = no subfolders

[sync]
interval = 3600           # seconds between syncs in daemon mode
workers  = 4              # parallel download threads
```

---

## Usage

**Download everything once:**
```
keepin sync
```

**Run in the background, syncing every hour:**
```
keepin daemon
```

**Check how many pins are saved:**
```
keepin status
```

**Register as a system service (auto-start on boot):**
```
keepin install-service
```
On Linux this creates a systemd user service. On Windows this creates a Task Scheduler task.

**Verbose output for debugging:**
```
keepin -v sync
```

---

## Output structure

```
pins/
  Board Name/
    928374910703924465_title-slug.jpg
    928374910703924466_another-title.jpg
  Another Board/
    ...
  .keepin/
    state.db        <- SQLite database tracking downloaded pins
```

---

## Notes

- keepin uses Pinterest's unofficial internal API. It may break if Pinterest changes their web app. If you get HTTP 403 errors, the session cookie has expired — refresh it from the browser.
- Only one dependency: `requests`.
- Tested on Windows and Linux.

---

## Contributing

Contributions are welcome! Whether it's bug reports, feature requests, or code improvements, feel free to:

- Open an issue to report bugs or suggest features
- Fork the repository and submit a pull request with your changes
- Test on both Windows and Linux if possible
- Keep changes focused and include clear commit messages

For major changes, please open an issue first to discuss what you'd like to modify.
