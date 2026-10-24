"""Deploy the current main to Railway on demand, and say plainly what happened.

Why this exists: Railway normally deploys on a GitHub push, through a webhook
the Railway GitHub App installs on the repository. That webhook was gone on
2026-10-24 (0 hooks on mavenpredict1987-afk/hl-sniper) and the platform answers
every deploy call - push triggered or manual - with "Deploys have been paused
temporarily", because the trial workspace spent its $5 credit. So this script
cannot fix the pause: it makes the state visible in one command and starts a
deploy the moment the workspace can build again.

Usage:
    python3 deploy.py            # ask Railway to deploy the latest main commit
    python3 deploy.py --status   # only print the last deployments

Token: .secrets/railway_token (project token, never committed).
Project/environment/service ids are fixed below; all three were read from the
API, not guessed.
"""
import json
import os
import sys
import urllib.error
import urllib.request

API = "https://backboard.railway.app/graphql/v2"
PROJECT = "97a5740d-6c34-4aae-b9e6-aa891815f379"
ENVIRONMENT = "3fe426be-2ca5-40ba-878d-e129d9928004"
SERVICE = "92e22228-d87c-4334-bd75-1663af5505ee"
TOKEN_PATH = "/data/user/0/com.deepseek.harness/files/Repo2/.secrets/railway_token"


def token():
    try:
        return open(TOKEN_PATH).read().strip()
    except OSError:
        sys.exit("no Railway token at %s" % TOKEN_PATH)


def call(query, variables=None):
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(API, data=body, method="POST")
    req.add_header("Authorization", "Bearer " + token())
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "hl-sniper-deploy/1.0")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return {"errors": [{"message": "HTTP %s: %s" % (exc.code, exc.read()[:300])}]}


def deployments(limit=5):
    data = call("""query($s:String!){ deployments(first:%d, input:{serviceId:$s}){
        edges{ node{ id status createdAt meta } } } }""" % limit, {"s": SERVICE})
    return [e["node"] for e in data.get("data", {}).get("deployments", {}).get("edges", [])]


def show_status():
    rows = deployments()
    if not rows:
        print("no deployments visible with this token")
        return
    for n in rows:
        meta = n.get("meta") or {}
        print("%s  %-8s %s  %s" % (n["createdAt"], n["status"],
                                   (meta.get("commitHash") or "")[:10],
                                   meta.get("reason")))


def deploy():
    data = call("""mutation($s:String!,$e:String!){ serviceInstanceDeployV2(
        serviceId:$s, environmentId:$e) }""", {"s": SERVICE, "e": ENVIRONMENT})
    errors = data.get("errors") or []
    if errors:
        msg = errors[0].get("message", "")
        if "paused" in msg.lower():
            print("RAILWAY PAUSED DEPLOYS: %s" % msg)
            print("this build cannot start until the workspace can build again")
            print("(trial credit spent; upgrade to Hobby or migrate to the new "
                  "account) - the running container is unaffected")
            return 2
        print("deploy failed: %s" % msg)
        return 1
    print("deploy requested: %s" % data["data"]["serviceInstanceDeployV2"])
    return 0


if __name__ == "__main__":
    if "--status" in sys.argv[1:]:
        show_status()
    else:
        code = deploy()
        show_status()
        sys.exit(code)
