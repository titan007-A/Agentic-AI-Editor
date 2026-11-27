# TikTok gateway

TikTok is blocked in India. The live Mac can't reach it, and neither can the PC.

The TikTok gateway fixes this. A second Mac (the M1) stays on with a VPN. The live Mac
sends TikTok traffic to the M1 over Tailscale. The M1 sends it out through the VPN.

With the gateway:

- A job or a feed with a TikTok link downloads and edits like any other link.
- The Researcher can search TikTok (tick "TikTok" on the Researcher page).

Only TikTok goes through the M1: TikTok videos, their details, and searches for TikTok
videos (search engines hide TikTok from India, so those searches go through the M1 too).
Everything else stays on the live Mac's own internet.

Nothing of the editor is installed on the M1. It only lets the live Mac in, for this one job.

```
live Mac (editor) --Tailscale, SSH tunnel--> M1 (VPN on) --VPN--> TikTok
```

## What you need

- The M1 Mac, on power, with internet.
- A VPN app on the M1, set to a country where TikTok works.
- Tailscale on the M1, signed in with the same account as the live Mac.

## Steps, in order

### 1. On the M1: Tailscale

1. Install Tailscale (App Store, or tailscale.com/download).
2. Sign in with the same account as the live Mac.
3. Open the Tailscale menu. The live Mac should be in the list.

### 2. On the M1: the VPN

1. Install your VPN app and sign in.
2. Pick a country where TikTok works (for example Singapore, or the US).
3. Turn on "Launch at login" and "Auto-connect", so the VPN is always on.
4. Let Tailscale work beside the VPN (see "What the VPN app must allow" below).
5. Open tiktok.com in Safari on the M1. It should open.

### 3. On the live Mac: make the key

Run this in the editor's folder on the live Mac:

```
scripts/macos-tiktok-gateway.sh key
```

It prints two lines to run on the M1. They look like this:

```
curl -fsSLO https://raw.githubusercontent.com/titan007-A/Agentic-AI-Editor/main/scripts/setup-tiktok-gateway.sh
bash setup-tiktok-gateway.sh --from 100.94.146.32 'ssh-ed25519 AAAA... aiedit-tiktok-gateway'
```

The key is safe to send in a chat. It only opens the tunnel.

### 4. On the M1: run the setup

1. Open Terminal (Spotlight: type "Terminal").
2. Paste the two lines from step 3. Press Enter.
3. If it asks for a password, type the M1's login password. (Nothing shows while you type.)

The script checks everything and fixes what it can:

- Tailscale is on (it prints the M1's address, 100.x.y.z).
- Remote Login is on (so the live Mac can connect).
- The M1 never sleeps.
- The live Mac's key is let in, for the tunnel only.
- TikTok opens from the M1 (so the VPN works).
- Tailscale still works with the VPN on.

Lines marked `[!!]` need you. Fix them, then run the second line again. It is safe to run
again any time.

At the end it prints a line like:

```
Gateway: yourname@100.101.102.103
```

Send this line to whoever runs the live Mac.

### 5. On the live Mac: start the tunnel

```
scripts/macos-tiktok-gateway.sh install yourname@100.101.102.103
```

This starts the tunnel, tests TikTok through it, and writes `TIKTOK_PROXY` in `.env`. The
tunnel starts again by itself after a restart or a network drop.

Then restart the editor when no job is running:

```
scripts/macos-service.sh restart
```

### 6. Check it works

1. Open the System page. Find "TikTok gateway".
2. "Tunnel: up" and "TikTok through it: answers" means all is good.
3. Start a job with a TikTok link. It should download and edit.
4. On the Researcher page, "TikTok" is now a choice. Tick it and search.

## What the VPN app must allow

Many VPN apps send all traffic through the VPN, Tailscale's too. Then the live Mac can't
reach the M1. The setup script checks this (step 7 in its output).

In the VPN app, find one of these (names differ from app to app):

- **Split tunnelling**: leave Tailscale out of the VPN.
- **Allow LAN** / **Local network access**: turn it on.
- **Allow Tailscale**: turn it on, if the app has it.

Also:

- The VPN must stay on all the time: "Auto-connect" and "Launch at login" on.
- If the app has a "kill switch", it must still allow the local network (Tailscale).
- After changing a setting, turn the VPN off and on, and run the setup again.

If your VPN app can't let Tailscale work beside it, use another VPN app that has split
tunnelling.

## Keep the M1 on

- Keep it on the charger. If it is a MacBook, keep the lid open.
- The setup turns sleep off. The screen may still turn off: that is fine.
- After a restart, log in to the M1 once. Tailscale and the VPN start only after login.
  (To come back alone after a power cut, turn on automatic login: System Settings >
  Users & Groups. This needs FileVault off.)

## When it breaks

The System page shows what is wrong. A failed TikTok job says it too.

| What you see | Why | What to do |
| --- | --- | --- |
| "Not set up on this server" | `TIKTOK_PROXY` is empty | Do steps 3 to 5. |
| "Tunnel: down" / "its tunnel is down" | The M1 is off, asleep, offline, or off Tailscale | Wake the M1. Check Tailscale is on. Wait one minute: the tunnel comes back by itself. Then "Check again". |
| "TikTok through it: no answer" / "the VPN is most likely off" | The VPN on the M1 is off, or its country blocks TikTok | Turn the VPN on, or pick another country. Run the setup on the M1 again to check. |
| Tailscale stops when the VPN is on | The VPN takes Tailscale's traffic | See "What the VPN app must allow". |
| TikTok answers, but videos fail ("Unable to extract...") | TikTok changed its site | System page > Update yt-dlp, then restart the editor. |
| The M1 got a new Tailscale address, or you use another Mac | The tunnel points to the old one | Run step 4 on the new Mac, then step 5 with the new line. |

Useful commands on the live Mac:

```
scripts/macos-tiktok-gateway.sh status    # is the tunnel running, does TikTok answer
scripts/macos-tiktok-gateway.sh test      # test TikTok through the tunnel
scripts/macos-tiktok-gateway.sh logs      # the tunnel's log
scripts/macos-tiktok-gateway.sh restart   # start the tunnel again
.venv/bin/aiedit tiktok-gateway           # the editor's own check
```

## Turning it off

On the live Mac:

```
scripts/macos-tiktok-gateway.sh uninstall
scripts/macos-service.sh restart
```

On the M1, remove the line ending in `aiedit-tiktok-gateway` from `~/.ssh/authorized_keys`.
You can also turn Remote Login off (System Settings > General > Sharing).

## Safety

- The live Mac's key can only open web connections (ports 80 and 443) through the M1.
- It can't open a shell or run commands on the M1.
- It works only from the live Mac's Tailscale address.
- The tunnel listens only on the live Mac itself (127.0.0.1), not on the network.

## Notes

- Opening a TikTok link in your own browser still needs a VPN on your phone or computer.
- Daily researches don't search TikTok; tick it when you want it.
- `TIKTOK_PROXY=direct` reaches TikTok without a gateway, for a server outside India.
