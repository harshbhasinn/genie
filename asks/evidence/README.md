# Evidence crops

Screenshots cut from `Agency Admin Dashboard (1.5).docx` at full resolution, for the two rulings
that are hardest to locate by description. Every one has been read at native zoom and verified.

---

## R17 · Campaign status vocabulary — nine words, four screens

| File | Words shown | Where in the deck |
|---|---|---|
| `R17-a-broadcasted-and-live.jpg` | **Broadcasted** (blue) · **● Live** (green) | **Brand Management** → under the **"BROADCASTING CAMPAIGNS"** label, top-right of the frame → the **second** screen in that group (the one with the *Delivery performance* line chart and the *67% DELIVERED* donut) → top card, right of the campaign name |
| `R17-b-active-and-scheduled.jpg` | **Active** (green) · **Scheduled** (yellow) | **Brand Management** → brand detail (Coca Cola) → **"Recent Campaigns"** card |
| `R17-c-active-and-paused.jpg` | **Active campaigns** · **12 paused** | **Home/Command Centre** → **Business Health** → the stat row *below* the four money cards |
| `R17-d-pending.jpg` | **Pending** · OTP | **Notifications and Approval** → Campaign Approvals → the blue info banner |

Not pictured, same Approvals screen: the **All / Approved / Rejected** filter chips above the table,
and **Draft**, implied by the *"Save to Draft"* button on every Launch Campaign step.

**The two questions for design:**

1. Are **Active** and **Live** the same state, or different?
2. Is **Broadcasted** a status at all — or is it the campaign *type* (Broadcasting vs Targeted)
   sitting in the status row? It appears immediately left of *Live*, which makes it read as a status.

**Keep separate:** Creative Library has its own three states — **Approved · Pending Review ·
Rejected** — describing an image or video file, not a campaign. Two state machines that share the
words "Approved" and "Rejected".

---

## R25 · Creative dimensions — landscape or portrait?

| File | What it says | Where in the deck |
|---|---|---|
| `R25-a-launch-wizard-1920x1080.jpg` | Static Ad — *10s · static image · **1920x1080*** · JPG/PNG · Max 5MB<br>Video Ad — *15s · full-motion · **1920x1080*** · MP4 · Max 10MB | **Launch Campaign** → Broadcasting flow → Step 1 *Campaign Details* → **"Creative Type"** section |
| `R25-b-upload-modal-1080x1920.jpg` | Static Creative — *plays as a still on the screen · **1080 × 1920 px (min)*** · JPG, PNG<br>Video Creative — *plays as a looping spot · **1080 × 1920 px*** · MP4 (H.264) | **Creative Library** → **"Upload Creative"** modal → *Step 1 of 2* |

**1920 × 1080 is landscape. 1080 × 1920 is portrait.** They cannot both be right.

A second, smaller mismatch in the same pair: the wizard constrains by **pixels + file size**
("Max 5MB"), the upload modal by **pixels only** — and the modal says **(min)** where the wizard
reads as a fixed size. Is that number a minimum, a maximum, or exact?

**A possible tiebreaker:** the **Live Preview** panel on Launch Campaign step 4 (*Campaign Summary*)
renders the screen as a **tall portrait rectangle** — the mockup with the store name, QR code and
stacked ad panels. If the physical screens are portrait, **1080 × 1920 is correct and the wizard's
1920 × 1080 is the error** — a one-line fix rather than a design debate. Worth a word with whoever
knows the hardware.
