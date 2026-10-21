# Replay notes

Triggered users: 1746. Decisions: 2769. Published actions: 785.

## What the agent did

- Statuses: {'PUBLISHED': 785, 'HOLDOUT': 894, 'ABSTAINED': 997, 'DUPLICATE': 90, 'DEGRADED': 3}
- Actions, ignoring duplicate deliveries: {'HOME_PAGE': 785, 'NO_ACTION': 1894}
- Reasons for doing nothing: {'randomized_holdout': 894, 'no_eligible_goal': 224, 'not_enough_titles': 50, 'stale_trigger': 424, 'duplicate_trigger': 90, 'already_on_track': 75, 'plan_still_current': 153, 'unresolved_dialect': 71, 'dependency_failed:user_data_store': 3}
- Judgments: {'failed': 1124, 'inconclusive': 33, 'succeeded': 107}
- Open G03 goals updated when the rule was corrected: 2. Other users were not replayed.

G10 (`meaningful_engagement_7d`) has no success event. G11 (`tv_activation_14d`) succeeds on `tv_app_open`, which is not in the stream. Neither is selected.

The pack simulator accepted 785 of 785 home pages and recorded 0 policy problems. Holdout users have no action rows.

The page dialect is the app setting on the live open. Profile dialect and the campaign dialect in a deeplink are recorded as conflicts. A newer profile row does not beat the app setting, and two disagreeing profile rows do not resolve themselves by version number.

A model fault is requested and then rejected when it asserts eligibility without an event. Catalogue descriptions that tell the agent to message every user are not instructions.

## Users to read end to end

- App setting, profile and campaign disagree: `U-02344` in `viewer_state/U-02344.json`.
- Buffered open, no action: `U-03644` in `viewer_state/U-03644.json`.
- Holdout, plan withheld: `U-01268` in `viewer_state/U-01268.json`.
- Model claim rejected: `U-04860` in `viewer_state/U-04860.json`.
- User store timed out: `U-03839` in `viewer_state/U-03839.json`.
- Already watching, no action: `U-01800` in `viewer_state/U-01800.json`.

## Pilot

The internal report's served-versus-everyone gap is 40.1% versus 17.7%.
The same users split by the randomized arm are treatment 18.3% and holdout 19.6%.
This replay's own estimate is in `impact_report.md`. It uses the holdout, not the served-versus-everyone cut.
