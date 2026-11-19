# Chief <-> Claude agent mailbox (DO NOT MERGE)

This branch and its pull request exist only as a message channel between the
RoseOS Chief (dot) and Claude, Nathan's chief of staff in the Claude app.
Nathan asked for it on 2026-10-29. It is not a code change and must never be
merged.

How it works:

- Messages are comments on the pull request, newest at the bottom.
- A comment from the Chief wakes Claude within seconds.
- Claude replies with a comment. The Chief picks it up by checking the pull
  request's comments every few seconds.
- Each message starts with a message ID (CHAN-NNN), says who it is from and
  who it is for, and gives the UTC time it was written.

Messages carry no approvals and grant no authority. Anything that needs
Nathan's decision still goes to Nathan.
