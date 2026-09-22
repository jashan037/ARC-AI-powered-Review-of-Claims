# ARC: 20-second teaser (storyboard)

Apple-trailer style: kinetic type, fast cuts, glass UI close-ups, one sound cue per cut. 16:9, 1920x1080, 60 fps.
Every figure on screen is real (from the sample claim and the project): ₹1,84,500 bill, about ₹1,22,125 likely payable,
₹20,500 on hold, 186 policy clauses, 8 answer checks, 68 non-payable items.

## Before you record

- Run the app live: `scripts/run_demo.sh`, open http://127.0.0.1:8765/ in Chrome, window 1920x1080, zoom 100%, bookmarks bar hidden.
- Record with **Screen Studio** (auto-zoom on clicks, smooth cursor). Its "zoom on click" gives the close-ups for free.
- Record each shot below as its own clip; you cut them together afterwards.
- Background music: a 120 BPM track (one cut = one beat = 0.5 s; two beats = 1 s). Sound effects: soft "whoosh" on type,
  "tick" on UI appear, one low "thump" at the logo.

## Shot list

| Time | Shot | Screen action to record | Text on screen (kinetic type) | Sound |
|---|---|---|---|---|
| 0.0-1.5 | Black. Type appears word by word, centre | none (title card) | **Health claims are confusing.** | low hum starts, tick per word |
| 1.5-3.0 | Hard cut, white | none | **Deductions. Waiting periods. Fine print.** (three words slam in, 0.4 s apart) | three ticks |
| 3.0-4.5 | The landing page, the hero headline wipes in | open `/` fresh (the entrance plays once) | none, let the headline read | whoosh |
| 4.5-6.0 | Close-up: the glass product card on the cyan band, slow push-in | hover over the band, stay still | **Meet ARC.** | thump, music drops in |
| 6.0-8.0 | Upload page, drop five PDFs; the glass loading card appears | drag the `demo/samples/on_time` PDFs onto the drop card | **Drop your documents.** | whoosh + tick |
| 8.0-10.0 | Chat, the first message with the dates and the bill | nothing, let it land; Screen Studio zooms on the text | **It reads everything.** | tick |
| 10.0-12.5 | Click "How much will be paid?"; the send ring spins; the answer with ₹1,22,125 | click the first chip | **About ₹1,22,125 likely payable.** then **₹20,500 on hold. Here's why.** | two ticks |
| 12.5-14.5 | Scroll the landing to "Built for Intelligent Claim Review"; the three cards rise | scroll `/` to the second screen | LED dots: **186 clauses. 8 checks. 68 items.** (one per 0.6 s) | tick x3 |
| 14.5-16.5 | "Download report", the PDF's first page | click "Download report", open the PDF | **A report for your insurer's team.** | page-flip |
| 16.5-18.5 | Architecture slide `docs/architecture.html`, slow zoom out | open the file full screen | **Python does the maths. The AI explains.** | whoosh |
| 18.5-20.0 | White, the ARC logo, then the line under it | none (end card) | **ARC.** / *Know where your claim stands.* | thump, music out |

## Tools, three ways to finish it

1. **Screen Studio + Keynote/iMovie (simplest).** Record the shots in Screen Studio, export each as MP4, put them on a
   timeline in iMovie or Final Cut, add the text cards as Keynote slides exported as video (Magic Move gives the kinetic type).
2. **Remotion (code, exact timing).** A React composition of 1200 frames at 60 fps; each row above is a `<Sequence from={...} durationInFrames={...}>`
   with an `<OffthreadVideo>` of the clip and a spring-animated `<AbsoluteFill>` of text. Good if you want the cuts on the beat to the frame.
3. **/brag (automatic).** The open-source Claude Code skill `latent-spaces/brag` reads a project and renders a short launch video with
   music via Hyperframes. It needs Node.js 22+, FFmpeg and `npx hyperframes`; install it yourself
   (`/plugin marketplace add latent-spaces/brag`, then `/plugin install brag@brag`) and run `/brag` in this folder. It skips
   secrets, but check its output for anything from `.env` before you share it. Use its video as a draft and swap in the real screen clips above.

## Rules for the cut

- Never show the terminal, `.env`, the Azure portal keys page, or a real person's documents. The sample documents are synthetic.
- Show "Amounts are estimates; your insurer's team makes the final decision" at least once (the landing note is enough).
- Keep every figure exactly as the app shows it.
