# Punctual

Time clock for the staff of one or more workplaces. Employees clock in and out on their phones at the site, and admins keep the record correct and auditable for labour inspections.

The code and these definitions are in English. The interface is available in English (default) and Romanian (`PUNCTUAL_LOCALE`); the Romanian UI word follows each term.

## People and places

**Employee** (angajat):
A person who clocks in and out. An admin is also an employee, with extra rights.

**Admin** (administrator):
An employee who corrects records, decides requests and reviews flagged days.

**Site** (locație):
A workplace where employees work. It has a position, an allowed radius and its own QR code. An employee can clock only at the sites assigned to them.

## The record

**Punch** (pontaj):
One recorded clock event: an **arrival** (sosire) or a **departure** (plecare) at a site, at a moment in time.
_Avoid_: check-in/check-out, entry, record

**Manual punch** (pontaj adăugat de admin):
A punch an admin entered on an employee's behalf, always with a reason.

**Void** (anulare):
An admin's decision that a punch does not count. The punch stays in the record and is marked void. Nothing in the record is ever changed or deleted.
_Avoid_: delete, remove

**Edit** (modificare):
An admin correcting a punch's time, kind or site. It is the void of the original plus a replacement punch linked to it.

**Live punch** (pontaj valabil):
A punch that has not been voided. Only live punches count.

**Local day** (zi):
A calendar day in the configured time zone (`PUNCTUAL_TIMEZONE`). Days, hours and reports are counted in local days.

## Shifts

**Shift** (tură):
An arrival paired with the departure that follows it. A shift counts on the local day it started.

**Shift state** (stare):
Where an employee stands right now:
- **closed**: not at work.
- **open**: at work since an arrival punch.
- **pending arrival**: at work since an arrival the employee declared and an admin has not decided yet.
- **forgotten**: an open shift whose cutoff has passed.

**Cutoff** (ora limită):
22:00 local time by default (`PUNCTUAL_FORGOTTEN_CUTOFF_HOUR`). It is the first one after the arrival. A shift still open at its cutoff is forgotten.

**Forgotten shift** (tură neînchisă):
An open shift that passed its cutoff. The employee cannot clock again until they declare when they left.
_Avoid_: stale shift, open shift (an open shift is not necessarily forgotten)

## Corrections and review

**Correction request** (cerere de corecție):
An employee declares a punch they forgot, with a time and an explanation. A pending request counts toward the shift state until an admin approves it (it becomes a manual punch) or rejects it.

**Flagged punch** (pontaj de verificat):
A punch made outside the site's radius or with an imprecise phone location. It is kept and counted, but an admin should look at it.

**Location approval** (locație aprobată):
An admin accepting a flagged punch's location. The flag goes away.

**Anomaly** (anomalie):
Something wrong in a day's shifts: a missing departure, a departure without an arrival, or a shift that is too long.

**Day review** (zi verificată):
An admin's note that they checked an employee's flagged day and accept it. If a different flag appears later, the day needs review again.

**Rejected attempt** (încercare respinsă):
A punch the server refused, for example because the employee was too far away. It is logged for admins but is not part of the record.

## Days off

**Public holiday** (sărbătoare legală, SL):
A Romanian legal holiday (the calendar is Romania-specific), computed in code (`punctual.holidays`), Orthodox Easter cycle included. No hours are expected and reports mark the day.

**Working day** (zi lucrătoare):
A local day that is neither a weekend nor a public holiday.

**Leave request** (cerere de concediu / zi liberă):
An employee asks for vacation (**VAC**; **Co**, concediu de odihnă in the Romanian UI), medical leave (**MED**; **Cm**, concediu medical) or a free day (**OFF**; **Zl**, zi liberă) over a range of local days. An admin approves or rejects it; the decision is final. Once approved, every working day it covers is marked with its code in reports and expects no hours. Weekends and public holidays inside the range are not counted.
