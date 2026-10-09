"""Intercom WebTab: an old iPad as a door-intercom station and weather panel.

The server part is split into small modules:

* ``config``  - INI loading and validation (``Config``);
* ``sip_uas`` - a tiny SIP user agent server on localhost that Asterisk rings;
* ``ami``     - Asterisk Manager Interface client (door opening, camera view calls);
* ``media``   - ffmpeg processes: camera streams, call audio, dummy video to the panel;
* ``calls``   - the call/view/open state machine;
* ``web``     - the aiohttp application and the background loops.

Nothing is started or read at import time; ``python3 -m intercom_webtab --config FILE``
loads the configuration and runs the server.
"""

__version__ = "0.1.0"
APP_NAME = "Intercom WebTab"
