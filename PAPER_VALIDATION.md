# Loss protection and subsequent 24-hour paper validation

Status: prepared; NOT STARTED. No execution settings changed and no deployment performed.

## Entry protections

`RiskConfig.max_daily_loss_pct` defaults to 2% and `max_drawdown_pct` to 5%.
Both accept finite percentages greater than zero and no greater than 100 via the
existing configuration API. Older stored configurations use these defaults.
Changing a limit does not clear a breached latch. Daily losses use broker day-start
equity, pinned to a New York calendar date. A breached daily latch blocks new entries
for that date even if equity recovers. The drawdown peak and lockout persist across
dates and restarts. Drawdown includes unrealized losses in broker account equity.
The initial migration takes the maximum of initial equity, day-start equity, current
equity and persisted equity snapshots. It cannot recover an unrecorded historical peak.

`risk_state` holds the peak, date baselines and breach latches. The account record
tracks initialization so missing global state fails closed. Recorded dates detect
missing daily state. Missing/invalid baselines, corrupt latches, stale broker account
data, unavailable storage, non-flat broker positions/orders and unresolved entry
reservations prevent new entries. Management of existing positions remains available.

All manual/autonomous `place_mleg` entries reserve the singleton Mongo `_id`
`entry_reservations/single_position` before broker submission. No timeout expires it.
An ambiguous error, partial fill or crash between fill and position persistence keeps
it blocked across restarts. A reservation is released automatically only after its
matching database position is closed (fresh broker-flat checks still apply before
another entry), or a confirmed terminal zero-fill cancellation/rejection. An existing
position on any underlying blocks new risk, regardless of the older preset concurrency
value. Funded spread entries are refused by this guard. Existing execution interlocks
and execution settings remain unchanged.

For a stuck reservation or drawdown lockout, investigate account identity, complete
broker order history, positions (including assigned stock), database positions and
audit records. Do not delete/reset records merely to resume entries. There is no new
automatic or public reset endpoint. Any intentional reset needs a separately reviewed
operational change after reconciliation.

## Run preparation and acceptance criteria

1. Review and merge the loss-protection PR through the normal release process.
   No release/deployment has been performed as part of this fix.
2. Verify an isolated persistent database, exact Alpaca paper endpoint, expected
   paper account identity and funded-trading interlock disabled. Save the reviewed
   commit and the current configuration/interlocks without exposing credentials.
   Do not run the existing integration `backend_test.py` against an unreviewed service:
   it can force a trading cycle.
3. Record UTC start and finish timestamps for a continuous 24-hour paper observation
   covering a regular trading session. Keep existing execution settings unchanged.
   If entries are disabled, report this as shadow/exits-only validation; it cannot
   establish paper entry execution. A change to enable paper entries requires a
   separate authorized operational step.
4. Collect account equity/day-start equity, persistent peak and daily baselines,
   latch values, entry reservations, broker positions/open orders, decisions and
   worker results throughout the run. Record a controlled restart and confirm peak,
   baselines and reservations survive. Never inject faults into funded trading.
5. Exercise cutoff/lockout/missing-data/concurrency fault scenarios in an isolated
   offline fixture (the automated suite), and correlate the paper run with real
   observations. Do not manufacture losses in the paper account to trigger a stop.
6. Accept only with no duplicate/simultaneous spread positions, no entry while a
   latch or unknown broker state blocks risk, no state reset on restart, no funded
   orders, no unexplained partial fills or unresolved reconciliation failures,
   and a complete 24-hour evidence interval. Keep any unresolved result blocked.
   Thresholds restrict new entries; they do not guarantee a maximum realized loss
   on existing positions or force liquidation.

Report actual start/end, observed cycles and entries, restarts, broker/database
reconciliation, incidents and limitations. Until that report exists, the 24-hour
validation is pending.
