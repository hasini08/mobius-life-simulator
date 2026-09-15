# Running the Portfolio Builder Game live — host guide

A quick-reference for whoever is hosting the game at the event. No coding knowledge needed —
this is the "what do I actually click, in what order" guide. For anything technical (setup,
architecture, known issues), see [README.md](README.md) instead.

**What the game actually is, in one paragraph**: each team builds their own retirement portfolio
— picking a mix of asset classes and how much to put in each — then finds out its "probability of ruin":
the chance, across 2,000 simulated futures built from real historical market data, that their
pot runs out of money before the plan is meant to end. Lower is better. Everyone plays the same
scenario (same starting age, pot, spend) so scores are directly comparable, and nobody sees their
own score until you, the host, reveal it to everyone at once — that's what makes it a game rather
than just a calculator.

**Game URL**: https://mobius-life-simulator-czyadc2asardjl7oiuigmz.streamlit.app *(Portfolio
Builder Game is the second page in the sidebar)*

**Host PIN**: _______________ *(set in Streamlit Cloud → app settings → Secrets as `host_pin`
— see README's "Host controls" section. Fill this in before the event and keep it somewhere
you can find on the day.)*

---

## Before the event (do this ~15 minutes beforehand)

- [ ] **Open the game URL yourself first.** Streamlit Cloud apps fall asleep after a period of
      inactivity — if the first person to load it during the event finds a "waking up" screen,
      that's a bad first impression. Loading it yourself 10-15 minutes early keeps it awake.
- [ ] **Log into host mode** (see "Logging in as host" below) and check the scenario shown looks
      right (age/pot/spend/etc). Publish a fresh one if not.
- [ ] **Clear the leaderboard** if there's old test data on it, so the event starts clean.
- [ ] **Do not push any code changes once people start playing.** Any push redeploys the app,
      which wipes the leaderboard (it's running on local storage, not the shared Google Sheets
      backend — see README's Known Gaps if that's since been set up).
- [ ] Have the game URL ready to share (QR code / short link / displayed on a screen).

## Logging in as host

1. Open the game page, expand **"⚙️ Game setup (host controls)"** near the top.
2. Enter the host PIN in the **Host PIN** field and press Enter.
3. You'll see "✅ Host mode" — from here you can set the scenario and, later, reveal the winner.
   Everyone else just sees a read-only summary of whatever you've published — they can't edit it.

## Running a round

1. **Set the scenario** (in host mode): starting age, pot, spend, time horizon. Defaults are
   reasonable if you don't want to customise. Click **"📡 Publish to all groups"**.
   Nobody sees the actual portfolio builder until you've done this — before that, everyone's
   looking at a "how to play" screen with a **"🔄 Check again"** button. Tell them to hit that
   once you've published; it unlocks their builder right there (with a little balloons moment).
2. **Tell everyone to build and lock in their portfolio.** Each team/person:
   - Enters a team name
   - Drags sliders (2% steps) or types an exact % for each asset class until they hit 100%
   - Clicks **"🔒 Lock in my portfolio"**
   - Their score is calculated immediately but stays **hidden** from them — this is deliberate,
     it's what keeps the suspense until you reveal.
3. **Watch progress.** The banner near the top shows "X portfolios submitted, Y teams playing"
   without spoiling any scores — a good visual cue for "how many are still building."
4. **When ready, reveal.** Scroll to the Leaderboard section (still in host mode) and click
   **"🎉 Reveal the winner to everyone."** Every device unlocks automatically on its next
   interaction — their own result, the crash-test buttons, the champion card, all of it.
5. **Want to run another round?** Click **"🔒 Hide scores again"** to re-lock without wiping
   history, or **"🗑️ Clear leaderboard"** for a completely fresh game (also re-hides scores
   automatically).

## What to expect / what to say if it's slow

- **A few seconds' delay after clicking Lock in or Reveal is normal** — it's running a real
  2,000-scenario simulation, not an animation. If lots of people click "Lock in" at the same
  moment, requests queue on one shared server, so the last few might wait up to ~20-30 seconds.
  If you're running this with a big group, consider staggering "lock in when you're ready"
  rather than "everyone click now."
- **Crash-test buttons** (after reveal, "Would your portfolio have survived...?") also take a
  few seconds each — they re-run the player's own portfolio to build that chart.
- **If someone sees a "Zzzz... this app has gone to sleep" screen**, click the button to wake it
  and wait ~30-60 seconds. This shouldn't happen if you kept it awake beforehand.

## If something looks broken

- **Leaderboard suddenly empty mid-event**: the app restarted (someone pushed code, or it went
  to sleep and woke back up). Nothing to fix live — just let people know scores reset and carry
  on; ask whoever's on the dev side not to push anything until the event's over.
- **A player says their portfolio "disappeared"**: if they did a hard refresh of the page (not
  just clicking buttons within it), that starts a fresh session and forgets their locked-in
  portfolio — it's still on the leaderboard, just not shown on their screen anymore. They can
  build again.
- **Someone else is in host mode who shouldn't be**: the PIN is shared knowledge once someone's
  seen it entered — if that's a problem, it can only be changed by editing Streamlit Cloud's
  Secrets (a dev-side fix, not something to do live).

## After the event

- Screenshot the final leaderboard if you want a record of it — it doesn't persist anywhere
  else unless the Google Sheets backend has been set up (see README).
