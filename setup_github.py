"""One-time helper: create a public GitHub repo and push the project.

Usage:  python setup_github.py
You will be asked for your GitHub username and a personal access token; paste
the token into the prompt (paste works here). The repo is created as PUBLIC.
The token is used only during this run and is not stored.
"""
import subprocess
import time

import requests


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], check=check, capture_output=True, text=True)


username = input("GitHub username: ").strip()
token = input("Personal Access Token (starts with ghp_): ").strip()
repo = (
    input("Repository name [data-extraction-pipeline]: ").strip()
    or "data-extraction-pipeline"
)
description = "OCR data extraction pipeline for invoices and CNIC cards (Flask + Google Gemini, Excel/CSV output)"

headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}
r = requests.post(
    "https://api.github.com/user/repos",
    headers=headers,
    json={"name": repo, "description": description, "private": False},
    timeout=60,
)
if r.status_code not in (200, 201):
    print(f"\nCould not create the repo. GitHub says: {r.status_code} {r.json().get('message')}")
    print("Check the username and that the token has the 'repo' scope.")
    raise SystemExit(1)
print(f"\nRepo created: https://github.com/{username}/{repo}")

push_url = f"https://{username}:{token}@github.com/{username}/{repo}.git"
git("push", "-u", push_url, "main")
print("Pushed.")

git("remote", "remove", "origin", check=False)
git("remote", "add", "origin", f"https://github.com/{username}/{repo}.git")
print("Done: https://github.com/{}/{}".format(username, repo))
time.sleep(0.1)