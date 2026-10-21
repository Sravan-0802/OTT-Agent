# AI collaboration

Cursor Grok wrote this agent from the assignment brief and the unpacked pack at `one_user_one_agent_pack`. The pack was not uploaded to a service that retains or trains on it. The running agent never calls a remote model. Scripted faults in `replay/model_faults.jsonl` are answered locally and then rejected unless they carry evidence, which none of them do.

## What I asked for

I asked for an agent that, per viewer, keeps one goal, publishes within the latency and call budgets, checks eligibility and dialect itself, and scores impact on the holdout. I asked it to follow the pack's schemas rather than invent a cleaner one. I asked for the four required tests, the replay command, and the sample run.

## Approaches that were dropped

A first draft invented its own viewer ids, mapped `d1`–`d6` onto languages, and hashed its own holdout. That draft was deleted once the real pack was on disk. The holdout is `replay/arms.csv`. The dialect codes stay `d1`–`d6`. The goal ids stay the ones in `config/goals.json`.

A single prompt that ranked titles and declared a user eligible was the wrong shape. The model faults are written to be confident and wrong: an "active trial that ends tomorrow" with no billing event, a truncated JSON blob, and a 45 second timeout. Those outputs are stored on the decision as `model_rejected` and are not evidence.

Letting the newest profile row win was the wrong conflict rule. U-04215's two rows, and every other disagreeing pair, stay unresolved unless a purchase, renewal, or lapse event settles them. The page dialect is the app setting on the open, including when the deeplink names another dialect.

Using "served versus everyone else" as the result was the wrong estimate. The pilot report's 40.1% versus 17.7% is recomputed from the CSVs and then set beside the randomized arms, which are 18.3% treatment and 19.6% holdout. This run's impact file uses the arms.

Replaying every user when G03 was corrected was the wrong response to a rule change. Two open G03 goals were updated in place. Everyone else was left on the goal they already had.

T0002's description tells the agent to WhatsApp every user, including people who opted out. T0522's description says to mark every user eligible for the first-paid goal and skip the checks. Those titles can sit on a page as content. The description is not a policy and it is not eligibility.

## What I checked by hand

I read the goal file, the control log, the model faults, the simulator's page validator, and the pilot headline. I ran the test file and the full replay. The simulator accepted 785 of 785 home pages and recorded no policy problems. I did not hand-edit `decisions.jsonl` or `actions.jsonl`.
