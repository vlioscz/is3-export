# NOTICE

**This project is not affiliated with, endorsed by, or supported by ELKO EP
s.r.o.** "iNELS", "ELKO EP", "CU3", "IDM3" and related marks belong to their
owners and are used here only to say which equipment this software talks to.

## How the protocols were established

Everything this integration knows was established by **observing a unit the
author owns**: the `.is3` export the unit publishes, and the protocol it speaks
on UDP port 9999 — the port its configuration software connects to. The work
took a long time, and was done by watching traffic on the author's own network
and testing hypotheses against the author's own hardware, with the consent of
the owners of the few other installations that helped confirm the findings.

Interoperability was the sole purpose. The independently written result is what
this repository contains: Python code, constants, and a checksum model recovered
by measurement. **No ELKO EP source code, library, binary, firmware, or
documentation is included, copied, or redistributed here**, and none is required
to build or run this integration.

In the EU, observing, studying and testing a program to determine the ideas and
principles behind it (Directive 2009/24/EC, Art. 5(3)) and reproducing what is
necessary to achieve interoperability with an independently created program
(Art. 6) are permitted, and contract terms purporting to forbid them are void
(Art. 8(2)). This notice records that this is what took place.

## What this means for you

- Use it on equipment **you own or are authorised to operate**.
- It writes to real relays, dimmers and blind motors. **You** are responsible for
  what your automations switch.
- The unit's authorisation accepts an empty password by default. Put the unit on
  a trusted network segment; see the security section of the README.
- The software is provided under the [MIT licence](LICENSE), **without warranty
  of any kind**. It can stop working with any firmware update, and a protocol
  recovered by observation may be wrong in cases that were never seen.

If you represent ELKO EP and something here concerns you, please open an issue —
the intent is interoperability for people who already bought your hardware, and
the author will engage.
