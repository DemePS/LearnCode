# "Retry exercice" button
It is in the exercice page, it should be on the feedback page

# Time to deploy  —  backend, pipeline and server are done (see deploy/README.md)
- [x] pipeline to build and deploy (GitHub Actions → GHCR → one host over ssh, no cloud console)
- [x] answer: the variables, secrets you need, and cheaper/scalable alternatives → deploy/README.md §3/§4

# GitHub user token: is it possible / needed past 100 users?
Answered in deploy/README.md §2. Short version: set a repository-level `GITHUB_TOKEN` (raises the
limit from 60/hour per IP to 5,000/hour). It is not a wall at 100 users; it is a wall without it.

# Where the DeepSeek key lives in prod
Answered in deploy/README.md §1: `/opt/learncode/.env` on the host, `chmod 600`, never in the image or
in CI. Rotate the key currently in `backend/.env` if it is real.

# Everything automated, no cloud portal
Done, except the three things nothing can create for you (deploy/README.md, "One-time setup"):
create the host, write its `.env`, and add the three deploy secrets to the repository.

# Known remaining risks (single-instance only, see deploy/README.md §5)
- progress feed and rate-limit windows are in memory → one container, no workers > 1
- no automated backup of the data volume
