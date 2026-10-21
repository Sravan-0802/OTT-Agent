# Known limitations

These are product choices and gaps in this submission. They are not hidden behaviour of the simulator.

**G07 is prose.** The file says a user started a show or micro-drama, has episodes left, and has not played for a while. The agent treats that as: a play of that slug, a remaining episode in `episodes.csv`, idle at least 3 days, and a title in the dialect of this open. A movie is not a series. "A while" is 3 days because the goal window itself is 3 days.

**"Recently lapsed" is not defined.** G06 matches a `subscription_lapsed` event inside 45 days, or a profile `plan_ends_at` inside 45 days on a lapsed row, or a profile that only says `lapsed` and has no end date. The last case is weaker and is written down as an assumption on the decision.

**G10 and G11 are refused.** G10's success event is null. G11's success event, `tv_app_open`, never appears in the stream. Refusing them is recorded on every decision.

**The only published action is a home page.** The brief allows a home page or one message channel or nothing. This agent publishes a home page on a live open, and nothing otherwise. It does not send push, WhatsApp, or an in-app message, including to a viewer who is inside quiet hours or over the daily cap. Consent and template checks are therefore unused. A buffered open does not become a message later.

**Goal order is a choice.** Continuation (G07), first play (G01), and first completion (G02) outrank the payment goals. Two triggered users have a `trial_active` profile. Both still had no episode completion, so they were given G02. G04 does not appear in this run. A different order would have put them on trial-to-paid.

**The model does not rank titles.** Ordinary decisions are rules. The mock is invoked only for the five triggers in `model_faults.jsonl`, including the two whose opens were buffered. Those five outputs are rejected. There is no path that sends viewer data to a hosted model.

**Outage latency is accounted, not slept.** An 8 second store read and a 45 second model timeout are added to `decided_in_ms`. The process does not wait. History is loaded once, before the timeline. A store timeout still publishes nothing, so that preload cannot leak into a decision the store refused to serve. A successful read during the degraded window does use the preloaded memory. This is a model of the outage, not a real p95.

**Ranking is play counts.** Rows are ordered by how often a `title_id` was started in the historical log, with a language match, a genre boost from the viewer's own plays, and a premium boost on payment goals. It is not a learned ranker. An instruction-bearing title can appear if it is otherwise eligible. Its description is never copied into an action.

**One replay day, one horizon.** Outcomes come from the pack simulator for 14 days after 2026-10-01 IST, seed `oua-practice`. A goal whose deadline is after 2026-10-15 22:59 IST is inconclusive. An untrusted clock on a matching event is inconclusive. The next unseen day and the evaluator's seed are not in this sample.

**State is a summary.** `viewer_state/<user>.json` stores the goal, the decisions, the resolved subscription, and the lessons. It does not store every raw event. There is no UI.

**Empty dialects abstain.** `d7` and `NA` occur on live opens and have no rows in the catalogue. The agent publishes nothing rather than borrowing titles from `d1`–`d6`.
