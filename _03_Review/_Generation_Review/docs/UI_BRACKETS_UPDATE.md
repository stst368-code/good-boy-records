# Rating brackets and captions — version 1.2.1

The centred reminder replaces the on-screen hotkey instruction, using the exact requested sentence. Keyboard scoring remains available. Gold U-shaped brackets span 1–2 and 3–4; score 0 and score 5 have independent centred captions. The six buttons keep their individual numerical scores and the original fourth-choice save behavior.

| Dimension | 0 | 1–2 | 3–4 | 5 |
| --- | --- | --- | --- | --- |
| Audio Quality | Pure noise | Rough | Clear | Perfect |
| Composition | Unrelated | Spin-off | Clearly inspired | Exact |
| Consistency | China shop bull | Inconsistent | Consistent | Unified Song |
| Realism | Slop | Could fool mum | Realistic | Hyper-realistic |

The supplied screenshot's red lines are interpreted as bracket placement only. The implementation uses the application's muted gold accent, placing each range label directly beneath its bracket and centred between its two buttons. Screen-reader labels include the corresponding new caption for each individual score.

The previous per-row questions, supplied cluster/genre and human-production explanation remain visible. The old numerical anchor captions are removed. At smaller widths captions wrap naturally; brackets resize with the button grid.

Browser validation uses temporary synthetic tracks at 1280 px desktop and 390/320 px phone widths. It checks alignment, visible guidance, absence of the hotkey line, no horizontal overflow, targets at least 44 px wide and 48 px high, automatic submission and review workflow behavior. Screenshots are used internally for inspection. Physical Windows/phone checks and real-corpus calibration remain outstanding.
