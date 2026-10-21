# Architecture

One viewer has one agent. That agent owns the viewer's active goal, the decisions already published, and the lessons learned when a fact arrives later. A second trigger for the same `event_id` does not publish again. A goal-rule change updates only the open goals of that id.

The replay is a single process. It loads the catalogue, the profiles, the arms, and the goal file, then folds `data/events.jsonl` into memory for the users who appear in the trigger file. Triggers, live events, and control events are then applied in `deliver_at` / `at` order. At the same timestamp, a control event lands first, then a live event, then a trigger, so a withdrawal or a rule correction is visible to the decision that shares its timestamp.

## Time

`received_at` is the server clock. `occurred_at` and `sent_at` share the phone clock. True time is `received_at` minus the time the phone held the event (`sent_at - occurred_at`). If `sent_at` is earlier than `occurred_at`, the clock is untrusted and the event is not used as a fact or as a live visit.

A hold of more than 600 seconds is an offline buffer. The agent records the trigger and publishes nothing. The pack simulator uses the same 600 second rule when it decides which sessions count as live.

## Facts, and which source wins

The page dialect is `app_dialect` on the live open. That is the dialect the app will request. `preferred_dialect` on the profile, and the dialect embedded in `ott://c/<campaign>/<dialect>/title/<title_id>`, are recorded when they disagree. A later `updated_at` does not make a profile the winner. Two profile rows that name different dialects stay conflicted.

Subscription status prefers a trusted `purchase_completed`, `subscription_renewed`, or `subscription_lapsed` event, walked in arrival order so the latest billing event stands. `is_trial: true` is a trial, which is not a paid plan. If there is no billing event and the profile rows disagree, the status is left unresolved. The warehouse snapshot is the morning before the replay (`snapshot_at` 2026-09-30 05:30 IST) and is marked stale even when its versions agree. A missing profile fails closed for consent and for billing. Signup time prefers a `signup_completed` event. Disagreeing profile signup timestamps are not averaged.

Catalogue text is content. A description that says to message every user does not change the action. Model output is requested only for the triggers named in `replay/model_faults.jsonl`. A claim of eligibility with no evidence, a truncated payload, a timeout, or an outage is recorded as rejected and is not a goal, a consent bit, or an approval.

## The goal

G10 has no success event. G11 succeeds on `tv_app_open`, which is not in the stream. Both are refused on every decision.

For a live open that names a dialect, the first matching rule below is the candidate:

1. G07, when a show or micro-drama in this dialect has a remaining episode and the last play of that slug was at least 3 days ago.
2. G01, signed up within 7 days and no `play_started`.
3. G02, at least one play and no `episode_completed`.
4. G12, a prior open exists and the latest one, excluding this trigger, was at least 14 days ago.
5. G03, signed up within 2 days.
6. G04, `trial_active` and at least one play.
7. G06, `lapsed`, and the lapse is recent (see limitations).
8. G05, `never` paid, at least one completion, and signup was not within 7 days.
9. G09, `active`, and plays on at most one IST day in the last 14.
10. G08, `active`, plays on 2 or 3 of those days, and the dialect has a title released in the last 30 days.

An open goal that is still eligible is kept. An open goal that is no longer eligible is closed as superseded, which is inconclusive, not a failure. If nothing matches and the subscriber already plays on four or more days with no unfinished series, the reason is `already_on_track`. A conflicted subscription with no content goal is `subscription_unresolved`.

On 2026-10-01T08:30:00Z the G03 success rule changes from `app_open` to `play_started`, still on a later IST calendar day. The correction rewrites the success fields of G03 goals that are still open, including goals opened earlier that morning, and the decision row that committed them. Closed judgments stay closed. Other goal ids are not reloaded.

## The action

On a live `app_open` or `login_success` the agent publishes one home page, or nothing. It does not also send a push, a WhatsApp, or an in-app message in that decision. Quiet hours (22:00–08:00 IST), the shared one-message-per-IST-day cap, consent, and the template list therefore have nothing to violate. Prices are not written, because a home page has no price field. The only prices in the pack are 1, 149, and 399 INR.

The arm comes from `replay/arms.csv`. A holdout user still receives a goal, so the comparison is the policy and not "who happened to be online". The page is withheld. No row is written to `actions.jsonl`.

A page the agent already published for this dialect inside the last 120 minutes is left in place. That is the page TTL the simulator uses. It is not a special case of the simulator's 3–40 second view delay.

Rows are built from titles that are in the page dialect and available at decision time. After 2026-10-01T07:00:00Z, T0001 is withdrawn. Each row has 3 to 10 titles and at most one `title_id` per `content_slug`, preferring `app_language`. Ranking is historical `play_started` counts, plus a genre boost from the viewer's own plays and a premium boost on payment goals. The hero is the G07 continuation, or a same-dialect deeplink whose description is not an instruction. A row is not stripped below 3 titles to make a hero. Dialects `d7` and `NA` have no catalogue rows, so the agent abstains. An open with no `app_dialect` abstains as `unresolved_dialect`.

## Verification

The publisher does not trust the planner's word. Before a page is emitted, eligibility is recomputed. The check fails closed when the goal is not the current eligible goal, a title is in the wrong dialect, a title is unavailable, a row has fewer than 3 or more than 10 titles, the page has more than 6 rows, or a slug is repeated. A failed check publishes nothing and does not commit a new goal. Generated text is not one of the checks.

## Latency and failure

`decided_in_ms` is compute time plus any dependency the control log charges. From 10:35 to 10:42 UTC the model provider is unavailable, so a scripted call in that window fails and the rules decide. From 13:10 to 13:17 UTC the user store is degraded: every decision in the window is charged 8000 ms, and a read times out when `sha256("store:{user}:{event}")` modulo 5 is 0. A timeout publishes nothing (`DEGRADED`) and still records the trigger so a duplicate cannot act later. The one scripted model timeout is charged 45000 ms. None of these waits are slept. The latency report says so. The budget is 60 seconds from the trigger. Five minutes is a failure. This run has no decision over 60 seconds.

## Measurement

A user enters the cohort on the decision that commits the goal, for both `PUBLISHED` and `HOLDOUT`. Stale opens, duplicates, store timeouts, unresolved dialects, and users with nothing to solve stay out.

After the simulator writes `outcome_events.jsonl`, each goal is judged from those events against the goal as it stands after the G03 correction. The window is strict: goal start < true time ≤ deadline. G03 also requires a later IST calendar day. G08 requires a release in the 30 days before the goal start. G09 requires plays on 3 distinct IST days. G04, G05, and G06 ignore `is_trial: true`. A matching payload with an untrusted clock is inconclusive. A deadline after the simulator's last session (2026-10-15 22:59 IST) is inconclusive, not a failure. Superseded goals are inconclusive and are left out of the rates.

The estimate is per goal, agent arm versus holdout, among cohort users with a succeeded or failed judgment. A pooled number is reported second because the goals have different base rates. The pilot's served-versus-everyone headline is recomputed from the pilot CSVs and is not this run's result.

## Scale, and what stays manual

At 1,000 opens a minute the same rules hold if each user is owned by one worker: the decision key is `event_id`, the goal lives with the user, and the catalogue is cached per dialect. At 10 million users and 40 dialects the worker still loads one user's events, not the population, and a dialect with fewer than three available titles still publishes nothing.

A person still has to change a goal definition, approve a new message template, set a price, and decide that a model may see production viewer data. The agent does not do those. It also does not treat a confident sentence as consent, eligibility, or a reason to contact the holdout.
