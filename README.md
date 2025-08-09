
# Username Checker

**Fast username enumeration across many sites — with concurrency, retries, proxies, and evidence-based verification.**

## 🚀 Features

* **Multi-site scanning** with a customizable `sites.yml` list.
* **Evidence-based hits** (`--evidence-only`, default) or loose matching (`--any-200`).
* **Concurrency** with per-domain limits and jitter to avoid bans.
* **Retry & timeout handling** with rotating headers from `headers.yml`.
* **Proxy support** (`http` / `socks`).
* **Bulk usernames** from file (`--userlist`).
* **Site filtering** with `--only` (comma-separated list).
* **Live link streaming** to a file (`--links-out`, default: `hits.txt`).
* **Export results** to JSONL or CSV.
* **Interactive mode** if no username provided.
* **Automatic defaults:** `--links-out hits.txt --evidence-only` enabled unless overridden.

---

## 📦 Installation

```bash
git clone https://github.com/YOURUSERNAME/username-checker.git
cd username-checker
pip install -r requirements.txt
```

---

## ⚙️ Requirements

* Python 3.8+
* `requests`
* `PyYAML`

Install dependencies:

```bash
pip install requests pyyaml
```

---

## 📂 Project Structure

```
username-checker/
│
├── main.py           # Main script
├── sites.yml         # List of sites and URL patterns
├── headers.yml       # Rotating headers (User-Agent, Accept-Language, etc.)
├── hits.txt          # Default live link output (created after scan)
├── requirements.txt  # Python dependencies
└── README.md         # This file
```

---

## 🖥️ Usage

### **Basic scan (default settings)**

```bash
python main.py johndoe
```

### **Scan multiple usernames**

```bash
python main.py johndoe janedoe
```

### **Scan from file**

```bash
python main.py --userlist usernames.txt
```

### **Limit to certain sites**

```bash
python main.py johndoe --only "GitHub,Twitter,Reddit"
```

### **Use a proxy**

```bash
python main.py johndoe --proxy socks5://127.0.0.1:9050
```

### **Export results**

```bash
python main.py johndoe --hits-out results.jsonl --csv-out results.csv
```

### **Loosen hit criteria**

```bash
python main.py johndoe --any-200
```

---

## 📝 sites.yml Format

Example:

```yaml
- name: GitHub
  url: https://github.com/{!!}
- name: Twitter
  url: https://twitter.com/{!!}
```

* `{!!}` or `{user}` will be replaced by the username.
* `evidence_regex` (optional) is used in `--evidence-only` mode to confirm real hits.

---

## 🛡️ How It Works

1. Loads `sites.yml` and normalizes format.
2. Spawns concurrent threads to check each site.
3. Matches content against `evidence_regex` (if enabled).
4. Streams confirmed links to `hits.txt` and optionally to JSONL/CSV.

---

## 📜 License

MIT License – free to use, modify, and distribute.

---

## ✨ Credits

Developed by **HackToLive Academy** community.

---
