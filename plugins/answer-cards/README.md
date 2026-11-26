# answer-cards

Opt-in (`clover plugins enable answer-cards`). Adds one small tool, `answer_card`
(~600 tokens of schema), for `forecast` and `schedule` cards. The tool description is the
guidance: use only when the user asked for that data and a card beats text.

Flow: model calls `answer_card` -> plugin validates a closed v1 record -> `acp_adapter/events.py`
copies it to `_meta.clover.card` on the tool call update (`kind: other`, same lane as
`_meta.clover.helper`). Other clients ignore unknown `_meta`.

Schema decision: cards are their own closed `{v:1,type,...}` records (types in
`integrations/generative-ui/contract.ts`), not extra comparison-table variants. The
`clover.structured-tool/1` record needs session/row identities that ACP does not have, and
its producer (`tools.structured_input`) does not exist in the repo. A converter to the
structured-tool envelope can be added when the desktop/iOS readers need it.

Real-path check: `CLOVER_HOME=<temp> python scripts/answer_cards_acp_e2e.py "show the weather"`.
