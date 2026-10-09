# Security policy

Intercom WebTab can open a door, so please report security problems privately.

## Supported versions

| Version | Supported |
|---|---|
| 0.1.x | yes |

Fixes are released as a new patch version. To update, run `git pull` and then
`sudo ./deploy/install.sh`.

## Reporting a vulnerability

- Use GitHub's **private vulnerability reporting**: on the repository page, open
  **Security > Report a vulnerability**. Only the maintainers can see the report.
- **Do not** open a public issue, pull request or discussion for a vulnerability.
- Include:
  - what an attacker can do, and from where (same LAN, Internet, SIP side);
  - the steps to reproduce, with the version (`python3 -m intercom_webtab --version`)
    and the relevant configuration with all secrets removed;
  - logs only after removing tokens, passwords, camera URLs, phone numbers and
    addresses.
- Please do not test against installations you do not own, and never try to open
  someone else's door.

We aim to acknowledge reports within 7 days and to agree on a disclosure date with you.
That is usually within 90 days, sooner when a fix is ready. Reporters are credited in
the changelog unless they prefer not to be.

## In scope

- Opening a door, or ringing or answering the station, without the token.
- Leaks of the token, secrets, camera credentials or SIP passwords: in responses, logs,
  pages or files.
- Ways around the door-opening safety rules, for example a demo call that opens a door,
  or an Open that reaches the wrong panel.
- Weaknesses in the installer, the systemd units, the generated Asterisk configuration
  or the firewall rules.

## Out of scope

- Anyone who knows the token. It is the key by design. See
  [docs/security.md](docs/security.md) for how to keep it off untrusted networks.
- Plain-HTTP interception when the page is exposed contrary to the documentation.
- Vulnerabilities in Asterisk, FFmpeg, iOS or the data providers themselves. Please
  report those upstream.
