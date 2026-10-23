# Good Boy Records

Writing, MiniMax generation, review, preparation and the public record player.

| Directory | Purpose |
| --- | --- |
| `_01_writing` | Songwriting drafts, lore, YAMLs, track/variant catalogues and caption references |
| `_02_generation` | Selection, frozen jobs and generated inbox |
| `_03_review` | Listening, ratings, observations, analysis and reviewed recordings |
| `_04_preparation` | Selected recordings, processing recipes, mastering trials, artwork and playback preparation |
| `_96_website` | Galaxy player, knowledge map and public Signal Observatory |
| `_97_app` | Shared app launcher and device commands |
| `_98_database` | Shared generation/review database |
| `_99_tools` | Native workers and YAML editor |

Launch the app with `_97_app/Start-Windows.bat`. Preview the public website with
`_96_website/Preview-Windows.bat` or `python _96_website/build.py --serve --open`.
The website builds only public metadata and interface files; media stays on R2.

Open **Preparation** in the app after flagging a reviewed recording for distribution.
The shared database journals its move into Stage 4 and retains the review history.
The [preparation and storage plan](_97_app/_plans/preparation-and-storage.md) lists
the implemented features, remaining worker integrations and decisions to review.
