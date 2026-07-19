# WPM bot.py Scheduler Merge

This merge utility updates the repository's current `bot.py` rather than
replacing it with an older copy.

It adds only:

- `Path` import
- `SchedulerHost` import
- Scheduler host initialization using `data/scheduled_jobs.json`
- Scheduler startup after slash-command synchronization
- Scheduler shutdown before Discord client shutdown

The script refuses ambiguous edits and creates:

```text
src/watch_party_manager/bot.py.before_scheduler_merge
```

before changing `bot.py`.

Run from the repository root:

```powershell
Set-Location 'F:\Projects\WatchPartyManager'
.\.venv\Scripts\python.exe .\merge_scheduler_into_bot.py
```

Then run:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

After confirming the tests pass, remove the backup and merge script:

```powershell
Remove-Item .\src\watch_party_manager\bot.py.before_scheduler_merge
Remove-Item .\merge_scheduler_into_bot.py
```
