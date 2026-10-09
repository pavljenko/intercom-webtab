# Contributing

Contributions are welcome. Bug reports and fixes, door panels that need different
timings, new radiation networks and translations of the docs all help. Please read this
first.

## Rules

1. **English only:** code, identifiers, comments, docs, UI strings, test data, commit
   messages. The leak check rejects Cyrillic, and keeping one language keeps the project
   reviewable.
2. **No personal or provider data.** Never commit:
   - real addresses or coordinates of your home;
   - public IP addresses;
   - SIP numbers or accounts;
   - tokens or passwords;
   - camera URLs, host names of your machines, names of people, or photos of your
     entrance.

   Use the neutral examples: Greenwich (`51.4779, -0.0015`, `Europe/London`), IP
   addresses from `192.0.2.0/24`, `198.51.100.0/24` or `203.0.113.0/24`, numbers
   `100` / `201` / `202`, and hosts like `camera-gate.local`. Strip logs before pasting
   them into issues.
3. **Dependencies:** Python 3.9+, the standard library, aiohttp and Pillow, nothing
   else. The page has no build step and no libraries.
4. **The page stays ES5** for iOS 9 Safari, under 250 KB. Bump `var VER` when you change
   it. See [docs/development.md](docs/development.md#page-rules).
5. **Safety rules of door opening are not negotiable.** A change must not let a demo
   call open a door, answer a call automatically, or send a digit to a panel other than
   the one on screen. Add a test for any change in `calls.py` or `ami.py`.

## Before you open a pull request

```sh
python3 -m unittest discover -s tests     # unit tests (page checks need Node.js)
python3 tools/e2e.py                      # end-to-end test with a fake Asterisk
python3 tools/leakcheck.py                # personal-data and secret check
```

Turn on the pre-commit hook once, so that the leak check runs before every commit:

```sh
git config core.hooksPath tools/git-hooks
```

You can keep a private denylist of your own sensitive strings (street, names, numbers)
outside the repository. The hook picks up `~/.config/intercom-webtab-guard/denylist.txt`
automatically. See [docs/development.md](docs/development.md#leak-check-and-pre-commit-hook).

If you changed generated assets, regenerate and commit them:
- `python3 tools/ringtones.py`
- `python3 tools/make_icons.py`
- `python3 tools/mock_dump.py`
- the `dots.js` copy in `index.html`

## Pull requests

- Keep each pull request to one topic, and describe what changed and why.
- For hardware-specific behaviour (a door panel, a provider), describe the device in
  general terms (make and model, or the protocol) and leave out your installation's
  details.
- Update the docs in `docs/` when behaviour or configuration changes, and add a line to
  [CHANGELOG.md](CHANGELOG.md).
- CI must pass: tests on Python 3.9–3.12, the end-to-end test, the leak check, the ES5
  check and gitleaks.

## Reporting bugs

Open an issue with:
- what you expected and what happened;
- the version;
- the relevant part of `journalctl -u intercom-webtab`, with tokens, numbers, addresses
  and camera URLs removed.

Report security problems privately, as described in [SECURITY.md](SECURITY.md).

By contributing, you agree that your contribution is licensed under the
[MIT License](LICENSE).
