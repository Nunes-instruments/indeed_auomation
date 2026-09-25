NUNES RECRUITMENT CONSOLE V11.10
=============================

TECHNOLOGY
----------
Backend:
  Python / Flask
  SQLite
  Direct Chrome DevTools Protocol connection

Frontend:
  Next.js
  React
  TypeScript
  Responsive professional recruitment dashboard

LOCAL ADDRESSES
---------------
Dashboard:
  http://127.0.0.1:5285

Backend API:
  http://127.0.0.1:5286

ONE-TIME SETUP
--------------
1. Keep your normal Google Chrome open.
2. Enable Chrome Remote Debugging and click Allow.
3. Sign in to Indeed Employer in that same Chrome.
4. Open the Employer Candidates / Applicants page.
5. Double-click START.bat.
6. Open the Recruitment Console.
7. Click Connect Chrome.
8. When Candidates is detected, click Activate monitoring.
9. Enter your company name and Gmail App Password.
10. Save settings.

CURRENT CANDIDATE CATCH-UP
--------------------------
V10 is configured to process the candidates already in the current Candidates
queue ONCE.

After that first catch-up pass is completed, V10 continues with:
  newly appearing candidates
  + limited retries for candidates whose resume/contact was temporarily unavailable.

To intentionally run the current queue once again, use:
  Process current queue once

This resets only the one-time catch-up marker. Duplicate protection still prevents
the same candidate/job acknowledgement from being sent twice.

IMPORTANT
---------
The connected Indeed page must be the real Employer Candidates/Applicants page.
V10 automatically checks all open Indeed tabs and ignores login/navigation tabs.

The console will wait safely if the account is logged out.

Candidate email is never guessed.
The acknowledgement is sent only when a valid candidate email is available.

START / STOP
------------
START.bat:
  first run installs dependencies and builds the Next.js dashboard.
  future runs start both services in the background and open the dashboard.

STOP.bat:
  stops both the Next.js dashboard and Python backend.

OLD VERSION
-----------
If V9 is still running:
  STOP_OLD_V9_5105.bat

DIAGNOSTICS
-----------
DIAGNOSE_V10.bat


V10.1 CHROME PERMISSION FIX
---------------------------
V10 opened a new Chrome DevTools WebSocket during repeated dashboard status
checks. Chrome therefore displayed "Allow remote debugging?" repeatedly.

V10.1 keeps ONE persistent Chrome DevTools connection for the entire local app
session.

Important:
- Dashboard refresh does NOT connect to Chrome.
- Monitoring checks do NOT create new browser-level connections.
- Candidate detection does NOT create new browser-level connections.
- Click "Connect Chrome" once after starting V10.1.
- Chrome may show one "Allow remote debugging?" dialog.
- Click Allow.
- The same connection is reused until V10.1 is stopped or Chrome is closed.

If Chrome itself is closed/restarted, click Connect Chrome once again.

NEW PORTS:
  Dashboard: http://127.0.0.1:5285
  API:       http://127.0.0.1:5286

Before starting V10.1, you can run:
  STOP_OLD_V10_5115_5116.bat


V10.2 FIXED SENDER
------------------
The sender account is fixed internally in the application configuration.
The dashboard no longer shows or allows editing of the sender email field.

Only the Gmail App Password is entered from the dashboard.

NEW PORTS:
  Dashboard: http://127.0.0.1:5285
  API:       http://127.0.0.1:5286

If V10.1 is still running, use:
  STOP_OLD_V10_1_5125_5126.bat


V10.3 CLEAN CONFIGURATION
-------------------------
Removed from the dashboard:
  - Current applicant catch-up
  - Pending one-time processing
  - Process current queue once

The one-time current-candidate catch-up remains enabled internally by default.
When monitoring is activated for a fresh installation, the currently visible
valid candidates are processed once automatically. After that, monitoring
continues with new applicants and permitted retry cases.

NEW PORTS:
  Dashboard: http://127.0.0.1:5285
  API:       http://127.0.0.1:5286

If V10.2 is still running, use:
  STOP_OLD_V10_2_5135_5136.bat


V10.4 CONNECTION / CANDIDATES FIX
---------------------------------
V10.4 cleans duplicate legacy Chrome helper code left by older builds.

Connect Chrome now:
  1. connects to the existing Chrome once,
  2. checks every open Indeed Employer tab,
  3. if a Candidates/Applicants page is already open, uses it,
  4. if an Employer dashboard/Jobs page is open, finds the Candidates link
     and navigates there automatically,
  5. saves the Candidates page and activates monitoring automatically.

If the account is still on the Indeed login page:
  - Chrome remains CONNECTED,
  - sign in normally,
  - V10.4 checks again automatically and activates Candidates after login.

There is no need to press Detect & Activate repeatedly.

STALE VERSION PROTECTION
------------------------
START.bat automatically stops older V10/V10.1/V10.2/V10.3 ports before
starting V10.4, so the browser cannot silently reopen an older dashboard.

V10.4:
  Dashboard: http://127.0.0.1:5285
  Backend:   http://127.0.0.1:5286


V10.5 - CURRENT NEW QUEUE ONCE, THEN LIVE
------------------------------------------
This release matches the live Indeed screen where candidates are shown under:

  Manage candidates -> New

ONE-TIME START:
- V10.5 remembers every candidate already present.
- It sends acknowledgements ONLY for candidates whose current Indeed row is
  marked New.
- It processes the current New queue in batches until the discovered New set
  has received one processing attempt.
- Existing non-New candidates are remembered but not emailed by the catch-up.
- Duplicate email protection remains active.

AFTER THAT:
- The initial catch-up is marked complete.
- Any candidate identity that appears for the first time after live start is
  processed automatically.
- Temporary resume/email extraction failures can retry up to the configured
  retry limit.
- A candidate/job already marked SENT will not be sent again.

IMPORTANT:
The sender is fixed internally.
A Gmail App Password still needs to be saved once in the dashboard before mail
can actually be delivered.

V10.5:
  Dashboard: http://127.0.0.1:5285
  Backend:   http://127.0.0.1:5286


V10.6 - JOB / CONTACT / RESUME EXTRACTION FIX
----------------------------------------------
Candidate list row:
- Preserves the exact Indeed "Applied to: ..." position before opening the
  candidate, so the dashboard no longer falls back to "the position" when
  Indeed's detail page omits the job heading.

Candidate detail:
- Waits for the Indeed SPA to finish rendering.
- Expands visible Resume / CV / Contact Information controls.
- Reads resume/contact text from visible panels and same-origin frames.
- Inspects Indeed resume links, embedded resume URLs and resume-related
  resources.
- Supports PDF/DOC/DOCX-style resume downloads within the local processing
  limit.
- Validates visible candidate email/contact details.
- Resume email remains first priority.
- If no resume email is available but Indeed itself exposes a valid candidate
  contact email, that verified contact address can be used.
- Phone number is also collected when available.

Sender:
- Fixed internally as nuneslead@gmail.com.
- The dashboard now shows Sender email as a read-only field before Gmail App
  Password.
- The sender address cannot be edited through the dashboard/API.

IMPORTANT:
The Gmail App Password must still be entered once and saved. Until then the
dashboard correctly shows Email Service = Not configured and no mail can be
delivered.

V10.6:
  Dashboard: http://127.0.0.1:5285
  Backend:   http://127.0.0.1:5286


V10.7 - REAL INDEED ROW CLICK FIX
---------------------------------
The previous build could report:
  Candidates active
but:
  0 visible / 0 candidates

because the live Indeed Manage Candidates screen can render applicants as
clickable React rows/cards rather than normal candidate hyperlinks.

V10.7:
- Reads real rows containing "Applied to:"
- Extracts candidate name directly from the row
- Preserves exact "Applied to: <position>"
- Generates a stable candidate identity from Indeed row attributes when present
- Falls back to name + job + location identity
- Uses a normal candidate URL if one exists
- Otherwise physically clicks the real candidate row/name in the already-open
  Chrome page using CDP user-gesture execution
- Reads the opened candidate Resume / Contact panel
- Extracts verified email / phone when Indeed exposes them
- Returns to the Candidates list and continues to the next row
- Keeps duplicate protection and one-time New catch-up behavior

EMAIL DELIVERY:
The fixed sender remains internal.
The Gmail App Password is still required once.
If Email Service shows "Not configured", scanning can work but mail CANNOT be
sent. V10.7 shows a clear warning and immediately sends pending READY messages
after a valid App Password is saved.

V10.7:
  Dashboard: http://127.0.0.1:5285
  Backend:   http://127.0.0.1:5286


V10.8 - HANDS-FREE STARTUP + SHADCN UI
---------------------------------------
Automatic startup / reconnect:
- START.bat starts the backend and dashboard.
- The backend automatically attaches to the already-running normal Chrome.
- No dashboard Connect Chrome click is required during normal operation.
- If no Indeed tab exists, the app opens the direct Manage Candidates / New URL.
- If Indeed requires sign-in, sign in once. After the redirect, the app finds
  Candidates and activates monitoring automatically.
- If Chrome or the CDP connection drops, the app retries automatically.
- Failed connection attempts are throttled to avoid repeated Chrome permission
  dialogs.

Chrome security note:
Chrome itself may require "Allow remote debugging" once for a newly started
Chrome browser instance. That browser-owned security approval cannot be safely
bypassed. Once allowed, V10.8 performs the remaining reconnect/navigation work
without requiring Connect/Open/Activate buttons.

UI / UX:
- Added reusable shadcn-style Button component using Radix Slot + CVA.
- Added Lucide icons and compact action states.
- Removed the normal Connect Chrome action from the dashboard.
- Connection cards now communicate automatic reconnect / automatic Candidates
  preparation instead of instructing the user to connect manually.

V10.8:
  Dashboard: http://127.0.0.1:5285
  Backend:   http://127.0.0.1:5286


V10.9 - GMAIL SEND / SEND_FAILED RETRY FIX
-------------------------------------------
The V10.8 screenshot can show VERIFIED EMAIL FOUND but SEND FAILED.

Two separate problems are addressed:

1. Gmail transport/authentication
   - Secure STARTTLS on port 587.
   - Secure SMTP_SSL fallback on port 465.
   - The fallback transport is selected BEFORE send_message, so one message is
     never intentionally sent twice.
   - Gmail login is verified when settings are saved.
   - The dashboard distinguishes:
       Not configured
       Authentication failed
       Gmail verified
   - Exact safe error reason is displayed without exposing the App Password.
   - Test Gmail checks authentication without sending an email.

2. Automatic retry bug
   - V10.8 automatic queue selected only READY.
   - A SEND_FAILED row was therefore never retried automatically.
   - V10.9 automatically retries both READY and SEND_FAILED rows.
   - After a corrected App Password is saved, pending failed messages are
     retried immediately.
   - "Retry failed" can also be used manually.

GMAIL APP PASSWORD
------------------
Use the Google-generated App Password for nuneslead@gmail.com.
Do not use the normal Gmail account password.
Spaces in a displayed App Password are removed automatically.

V10.9:
  Dashboard: http://127.0.0.1:5285
  Backend:   http://127.0.0.1:5286


V11.0 - LIVE MONITOR + COMPLETE UNSENT OUTBOX
----------------------------------------------
Removed from Settings:
  - Check interval
  - Candidate batch size

They are no longer user settings.

LIVE CANDIDATES
---------------
Candidate monitoring runs continuously as an internal service.
There is no candidate batch limit.

New candidates:
  - detected and processed automatically.

Resume/contact not available:
  - the candidate remains in review.
  - V11.0 keeps rechecking unresolved candidate details indefinitely using an
    internal adaptive backoff.
  - this retry timing is not exposed to the user.

LIVE EMAIL OUTBOX
-----------------
Mail reconciliation is independent from candidate scanning.

Any applicant that has:
  - VERIFIED_EMAIL_FOUND
  - a verified/approved Indeed email source
  - and is not SENT / DUPLICATE_SKIPPED

remains in the live outbox.

That includes older statuses such as:
  READY
  SEND_FAILED
  NOT_SENT (when a verified email is now present)

So a temporary send failure does not cause the acknowledgement to be forgotten.

When Gmail is healthy:
  - V11.0 continuously flushes all pending verified acknowledgements.
  - there is no mail batch-size setting.

When Gmail is unavailable:
  - it stops hammering Gmail after a transport/authentication failure.
  - Gmail health is rechecked automatically.
  - once Gmail recovers, unsent verified acknowledgements resume automatically.

V11.0:
  Dashboard: http://127.0.0.1:5285
  Backend:   http://127.0.0.1:5286


V11.1 - ACCURACY + PERMANENT SENT RESPONSE HISTORY
----------------------------------------------------
1. ONE RECIPIENT = ONE ACKNOWLEDGEMENT
   The permanent sent_responses ledger uses recipient email as a unique key.
   If that email was already acknowledged, any duplicate Indeed row is marked
   DUPLICATE_SKIPPED and is never emailed again.

2. SINGLE MAIL DISPATCHER
   Candidate scanning only extracts/updates applicant data.
   It no longer sends email.
   The dedicated outbox is the only automatic sender.
   Every send uses an atomic SENDING claim so the background worker, manual
   Send button and settings retry cannot send the same applicant concurrently.

3. INTERRUPTED-SEND SAFETY
   If the program stops while an SMTP send is in an uncertain state, V11.1
   changes it to SEND_UNCERTAIN on restart and does NOT automatically resend.
   This is intentionally conservative to prevent a possible duplicate.

4. PERMANENT SENT RESPONSES
   The dashboard has a new Sent Responses section containing:
     candidate
     email
     phone
     position
     exact subject/message snapshot for V11.1+ sends
     sent date/time
     SMTP transport

   Historical SENT rows from previous versions are automatically backfilled.
   Old exact message text was not stored by previous versions, so those entries
   show the recovered recipient/job/time with a historical-message notice.

5. PERSISTS AFTER SERVER OFF / PC RESTART / FUTURE ZIP UPDATE
   Database and settings now use:
     %LOCALAPPDATA%\NunesRecruitmentConsole

   On first V11.1 start, if that shared database does not exist, the program
   searches the current/sibling/Desktop/Downloads previous Recruitment Console
   folders and copies the newest existing automation.db automatically.

6. ACCURACY GUARDS
   False Indeed UI names such as:
     All Open And Paused Jobs
     Education
     Yes / No
   are blocked.

   A verified email is NOT sent while job title is still:
     the position

   The candidate remains in review until the real Indeed Applied-to role is
   available.

V11.1:
  Dashboard: http://127.0.0.1:5285
  Backend:   http://127.0.0.1:5286


V11.2 - PRODUCTION ACCURACY + AUTOMATION RECOVERY
--------------------------------------------------

RESUME / EMAIL ACCURACY
-----------------------
V11.2 searches all available applicant contact sources:

1. Explicit Indeed contact/mailto values.
2. Full downloaded/rendered resume.
3. Complete DOCX content:
   - paragraphs
   - tables
   - headers/footers
   - Word XML/text boxes
4. PDF text using both pypdf and PyMuPDF.
5. Candidate profile/body text.
6. Hidden/rendered Indeed page HTML contact data.
7. Conservative binary text fallback for difficult/legacy files.

Common resume formatting such as:
  name @ gmail.com
  name@gmail . com
  name [at] gmail [dot] com
is normalized ONLY when all real components are already present.

Gmail addresses receive a small ranking preference because they are common in
the current applicant pool, but V11.2 accepts any valid real candidate email.
It never fabricates or guesses an email that was not present in source data.

ROLE ACCURACY
-------------
Role lookup uses:
  - Applied to: <role> from the candidate row
  - candidate detail Job/Position text
  - Indeed job-title elements/links

If role is still generic ("the position"), email sending remains blocked until
the real role is recovered.

When a duplicate/older record with the same verified email had "the position",
V11.2 repairs that record after a later scan discovers the real role.

PRODUCTION AUTOMATION CONTROL
-----------------------------
The dashboard now has one master control:

  Start Automation
  Turn Off Automation

When ON:
  - Chrome/Indeed reconnect runs
  - new applicants are monitored
  - unresolved resumes/contact data are retried
  - verified unsent messages are delivered
  - sent-recipient duplicate protection remains active

When OFF:
  - applicant scanning stops
  - automatic email sending stops
  - stored data/history remains intact

WINDOWS RESTART RECOVERY
------------------------
START.bat registers a Windows Task Scheduler task for the current user.
After the next Windows sign-in, the console backend/UI start in the background.

The ON/OFF state is stored under:
  %LOCALAPPDATA%\NunesRecruitmentConsole

So if Automation was ON before a reboot, it resumes after Windows sign-in.
If Automation was OFF, it remains OFF.

IMPORTANT PHYSICAL-POWER LIMIT
------------------------------
A local Windows program cannot execute while the PC is physically powered off.
No code can send an email from a computer with no power.

V11.2 therefore provides the production-safe local behavior:
  - nothing is lost while the PC is off
  - after Windows is powered on/signs in, the console auto-starts
  - if Automation is ON, it scans the missed/new Indeed applicants and sends
    any verified acknowledgement that has not already been sent

For literal email delivery while this PC remains powered OFF, the backend must
be moved to an always-on server/cloud/VPS/NAS. That is a different deployment
architecture and is not falsely simulated in this local release.

V11.2:
  Dashboard: http://127.0.0.1:5285
  Backend:   http://127.0.0.1:5286


V11.3 - STRICT APPLICANT MATCH + FAST LIVE + FIXED PANELS
----------------------------------------------------------

CRITICAL SEND RULE
------------------
V11.3 sends an acknowledgement ONLY when BOTH belong to the SAME applicant:

  1. verified candidate email
  2. exact applied role

If either one is missing or ambiguous:
  - status remains review/pending
  - email is NOT sent

EMAIL ACCURACY
--------------
Recipient evidence is intentionally restricted to:
  - the selected applicant's complete resume
  - explicit mailto/contact values in the selected applicant detail
  - contact/resume text in the selected applicant detail/drawer

V11.3 DOES NOT use the entire Indeed page/HTML as an email source. That older
method could accidentally see another applicant or the employer account.

Multiple real addresses:
  - explicit applicant contact wins
  - otherwise the address must clearly match the candidate name
  - ambiguous multiple addresses are held for review
  - no address is guessed

ROLE ACCURACY
-------------
Role is recovered from:
  - Applied to: ROLE on the row
  - split-line Applied to / ROLE layouts
  - role/job link inside the applicant row
  - selected applicant detail panel
  - selected applicant job link/title

A candidate row is still processed when the row role is temporarily missing;
the detail panel gets another chance. Sending remains blocked until role is real.

FAST LIVE APPLICANT FLOW
------------------------
After initial reconciliation:
  - a lightweight top/visible New-candidate pass runs approximately every 2 sec
  - new/unseen applicants are processed before old review records
  - verified email + verified role enters the single locked outbox immediately
  - Gmail sends it on the next outbox pass

Old unresolved resumes are handled in a separate full reconciliation pass so
they do not delay a newly applied candidate.

FIXED SCROLL UI
---------------
The Applicants table now has a fixed-height internal scroll box with sticky
column headers. Sent Responses and Recent Events also use internal scroll
areas. This keeps Settings / Message sections close instead of creating one
extremely long browser page.

IMPORTANT VERSION CHECK
-----------------------
V11.3 dashboard:
  http://127.0.0.1:5285

If the sidebar says V11.0 / V11.1 / V11.2 or the URL uses 5215/5225/5235,
you are still running an older server.

START.bat stops the older local ports before starting V11.3.


V11.4 - PRODUCTION WATCH + ONE-TIME ROLE CORRECTION + FIXED UI
---------------------------------------------------------------

CRITICAL V11.3 FIXES
--------------------
1. Applicants now really uses the fixed 430px internal scroll box.
2. Candidate extraction now executes build_candidate_payload_js(entry), so the
   selected applicant name + row role are bound into the detail extractor.

NEW APPLICANT MONITOR
---------------------
- pinned to Indeed New candidate queue
- returns to top before fast scans
- waits for Indeed SPA rendering before reading rows
- fast check every ~2 seconds
- newly appearing applicant gets priority over old review records
- current-New applicant with a loading/open failure is retried after ~10 sec

STRICT SEND
-----------
No normal acknowledgement is sent until the same applicant has:
  verified real email + exact applied role.

ONE-TIME OLD-MISTAKE CORRECTION
-------------------------------
V11.4 flags only old recipients with evidence the earlier acknowledgement used
"the position" (stored message snapshot, generic stored role, or historical log
that explicitly says "... for the position").

When the exact role is recovered:
  - one clarification message is sent
  - correction is permanently marked SENT
  - it cannot be sent a second time

FIXED UI
--------
Applicants: fixed 430px internal scroll + sticky header.
Sent Responses: fixed 300px internal scroll.
Recent Events: internal scroll.

V11.4:
  Dashboard: http://127.0.0.1:5285
  Backend:   http://127.0.0.1:5286


V11.5 - EXPLICIT TODAY LIVE DETECTION WATCHDOG
-----------------------------------------------

V11.4 already scanned the New applicant queue in the background, but the UI did
not prove clearly whether the latest scan was succeeding.

V11.5 adds a production live watchdog.

The dashboard now shows:
  Live detection: Watching New applicants / Starting / Recovering / Stale / Off
  Seconds since latest successful Indeed check
  Applicants detected today
  Acknowledgements sent today
  Current visible New-queue records
  New applicants found on the latest scan
  Latest applicant detected today
  Current scan mode (fast new watch or full accuracy reconciliation)

HEALTH RULE
-----------
A live check is HEALTHY only if the latest successful Indeed scan is recent.
If successful scans stop for approximately 15 seconds, the dashboard changes
from LIVE to STALE/ERROR instead of continuing to show a misleading green
Automation state.

TODAY COUNT
-----------
"Detected today" means applicants first detected by this Recruitment Console on
the current Windows machine's local calendar date.

It does not guess an Indeed application timestamp that was not available.

LIVE CADENCE
------------
Fast New-queue watch: approximately every 2 seconds.
Full unresolved-record reconciliation: approximately every 180 seconds.
UI dashboard refresh: approximately every 2 seconds.

The existing strict send rule remains unchanged:
  same applicant + verified email + exact role = send
  otherwise = do not send

Dashboard:
  http://127.0.0.1:5285

Backend:
  http://127.0.0.1:5286


V11.6 - DEDICATED VIEW NAVIGATION FIX
-------------------------------------

The V11.5 sidebar used simple hash anchors on one very long page and Overview
was permanently styled as the active item.

V11.6 uses real in-app view navigation:

  Overview
  Candidates
  Sent Responses
  Messaging
  Settings
  Activity

Only the selected view is shown.

OVERVIEW / MAIN LANDING
-----------------------
The full production Live Detection watchdog, status cards, setup warnings and
today metrics appear only on the Overview landing page.

OTHER VIEWS
-----------
Candidates:
  fixed internal candidate list only

Sent Responses:
  permanent sent/correction history only

Messaging:
  acknowledgement template editor only

Settings:
  company + Gmail configuration only

Activity:
  recent monitoring/extraction/delivery events only

The compact Live Detection status remains visible in the left sidebar on every
view so monitoring status is never lost.

Dashboard:
  http://127.0.0.1:5285/#overview

Backend:
  http://127.0.0.1:5286


V11.7 - VERIFIED APPLICATION + SKIP AUDIT + FAST DELIVERY
----------------------------------------------------------

SEND GATE
---------
A normal acknowledgement can enter the mail outbox only when all checks are
satisfied for the same Indeed applicant:
  1. exact candidate/application detail is verified
  2. real candidate email is verified from resume/contact evidence
  3. exact applied role is verified

MISSING EMAIL
-------------
If the selected application and resume are successfully readable but no valid
candidate email exists, the record is permanently marked SKIPPED with the
reason shown in the Candidates view. No mail is sent.

If the resume itself has not loaded yet, the record remains WAITING rather than
being incorrectly skipped.

AUDIT
-----
Candidates view shows Application / Mail / Reason so SENT, SKIPPED, READY and
waiting records are immediately understandable.

START / STOP
------------
Two explicit buttons are provided: Start Automation and Stop Automation. The
ON/OFF state persists.

WINDOWS RESTART RECOVERY
------------------------
Windows Task Scheduler auto-start remains enabled. After Windows sign-in, if
automation was ON, the console catches applicants that arrived while the PC was
off.

A physically powered-off PC cannot execute Chrome/Indeed/Gmail automation.
True sending while this PC remains powered off requires an always-on server/VPS.

FAST WATCH TARGETS
------------------
New queue polling: about 1 second.
Mail outbox polling: about 1 second.
Current-New retry after transient loading: about 3 seconds.
Dashboard refresh: about 1 second.
External Indeed/Gmail latency can make end-to-end delivery longer.

Dashboard: http://127.0.0.1:5285/#overview
Backend:   http://127.0.0.1:5286


V11.8 - FAST OPEN / FAST INSTALL
--------------------------------

The previous START.bat ran:

  pip install -r requirements.txt

on every launch. Even when every package was already installed, pip still spent
time checking/resolving the environment before the dashboard opened.

V11.8 separates INSTALLATION from NORMAL OPENING.

NORMAL START
------------
Double-click:

  START.bat

Fast path:
  1. If V11.8 is already running -> just open the dashboard.
  2. If cached runtime is valid -> skip pip.
  3. Skip npm install.
  4. Skip Next.js build.
  5. Start backend + dashboard in parallel.
  6. Close the START window immediately.

FIRST RUN / NEW PC
------------------
Only if the fast runtime is missing:

  - V11.8 searches existing V11.x folders in:
      current parent folder
      Downloads
      Desktop

  - If a compatible previous .venv exists, it is reused directly.
  - If a compatible previous node_modules + .next production build exists,
    they are reused directly.
  - Only missing components are installed/built.

If nothing reusable exists, Python/Node dependencies are prepared once and
cached under:

  %LOCALAPPDATA%\NunesRecruitmentConsole\FastRuntime

Later V11.x versions can reuse that cache.

MANUAL REPAIR
-------------
Only use this when setup/runtime is damaged:

  INSTALL_OR_REPAIR_ONCE.bat

Do NOT use the repair installer for normal daily opening.

PORTS
-----
V11.8 intentionally keeps:
  Dashboard: http://127.0.0.1:5285/#overview
  Backend:   http://127.0.0.1:5286

Those are the V11.7 ports. Keeping the same ports allows V11.8 to reuse an
already-built compatible V11.7 dashboard instead of forcing a new Next.js build.

PRODUCTION LOGIC
----------------
Applicant verification, live detection, skip reasons, SENT history,
one-recipient duplicate protection and the application/email/role send gate are
unchanged from V11.7.


V11.9 - ROLE REVIEW WORKSPACE
-----------------------------
The acknowledgement/live-monitor flow remains unchanged.

After a verified applicant is detected:
  application -> resume/contact -> verified email + exact role -> thank-you

Role Review adds a separate background evidence workflow:
  role -> official job description -> resume facts/evidence -> human review

ROLE BUTTON / SELECTOR
----------------------
The Role Review page automatically lists verified role titles found in Indeed.
Select a role to see every applicant for that role.

JOB DESCRIPTION
---------------
Paste the official description/requirements for each role once. The program
stores it persistently and checks the resumes against individual requirement
lines.

WHAT THE REVIEW ENGINE DOES
---------------------------
It shows:
  candidate
  thank-you status
  application verification
  resume facts
  requirement-by-requirement evidence snippets
  requirements where no clear resume evidence was found

It does NOT produce an automated hiring rank or decide eligibility.

MANUAL SHORTLIST
----------------
The Role Review view contains two fixed-scroll boxes:
  All applicants
  Manual shortlist

Your team can click:
  Manual shortlist
  Review later
  Not shortlisted

These are human selections stored by the console. The system itself does not
choose who should be shortlisted.

GENDER
------
V11.9 does not infer sex/gender from names, photos or resumes and does not use
gender to rank/shortlist applicants. Hiring review is kept role/qualification
focused.

FAST SETUP FIX
--------------
V11.9 fixes the V11.8 first-run crash:

  TypeError: find_python_runtime() missing 1 required positional argument

The prepare path now correctly calls:

  find_python_runtime(system_python)

Existing compatible Python/Node dependencies are still reused whenever
possible. Because V11.9 adds a new Role Review frontend, the dashboard may need
one production build on the first V11.9 run; normal later opens use the cached
runtime/build.


V11.9.1 - NEXT.JS 16 REUSED NODE_MODULES BUILD FIX
---------------------------------------------------
Fixed the first-run error:

  Error: Could not find the Next.js package (next/package.json)
  Turbopack build failed

Cause:
V11.9 correctly found node_modules from an older V11.x installation, but it
only passed that external folder through NODE_PATH. Next.js 16 Turbopack uses
a hermetic workspace and refuses to resolve the framework package from outside
the current frontend root.

V11.9.1 fix:
1. Reuse the previous V11.x node_modules without downloading it again.
2. Create frontend\\node_modules as a local Windows directory junction to the
   reused package tree.
3. Build from the current frontend using its local next package path.
4. Use Next.js --webpack compatibility mode for this reused-package build,
   avoiding Turbopack's external-workspace restriction.
5. If Windows cannot create the junction, use a fast local robocopy fallback.
6. npm install is used only when no compatible cached packages exist anywhere.

Normal future starts still skip pip, npm install and Next build after the
runtime passes the cache check.


V11.10 - AUTOMATED ROLE RANKING + JSON ERROR RECOVERY
------------------------------------------------------

AUTOMATED ROLE RANKING
----------------------
After the existing live applicant + acknowledgement flow:

  applicant detected
  -> exact Indeed application verified
  -> resume/contact processed
  -> thank-you logic runs exactly as before
  -> official Indeed job description is synchronized automatically
  -> resume is compared with role requirements
  -> evidence score is calculated
  -> candidate receives a role rank
  -> strongest evidence matches enter AI SHORTLIST

No manual shortlist buttons and no manual job-description setup are required.

ALL ROLES
---------
Roles are synchronized from verified Indeed applications. The background role
worker also periodically opens a temporary Indeed Employer Jobs tab, retrieves
available official job descriptions, and closes the temporary tab.

NEW CANDIDATES
--------------
New applicants are analyzed immediately after the live candidate scan whenever
resume + role description are ready. If either is still loading, they remain in
WAITING and the background worker picks them up automatically.

COMPLETED CANDIDATES
--------------------
A candidate whose resume + job-description fingerprints have already been
analyzed is skipped on later cycles. The analysis runs again only when the
resume or official role description changes.

RANKING METHOD
--------------
The score uses only job-relevant information:
  official job requirements
  skills/experience/education evidence stated in the resume
  exact applied role

Required/mandatory items receive more weight. Preferred items receive less
weight. All candidates remain visible even when the evidence score is low.

The automatic shortlist threshold is 70% evidence score.

GENDER
------
Gender/sex is not inferred or used in employment ranking. The two ranking boxes
are:
  AI Shortlist
  Remaining Candidates

JSON / INTERNAL SERVER ERROR FIX
--------------------------------
Frontend requests no longer call response.json() blindly. If the backend ever
returns plain text/HTML, the UI shows a readable HTTP/backend message instead
of:
  Unexpected token 'I', "Internal S"... is not valid JSON

The dashboard and Flask global error handler also return JSON on backend errors,
so one role-review error cannot break the whole Overview page.

V11.10 keeps the V11.9.1 local node_modules/Turbopack build fix.


V11.10.1 - INSTANT OPEN / NO BLOCKING LOADER
---------------------------------------------
The full-screen "Loading secure local workspace" screen has been removed.
The dashboard shell renders immediately, then live data hydrates in the
background.

OPENING SPEED CHANGES
---------------------
1. Daily START.bat uses --quick-check instead of the expensive full runtime
   validation. It does not import every Python dependency or hash all frontend
   source files on every start.
2. Frontend and backend still start in parallel.
3. The browser UI renders immediately with safe local defaults/cached status.
4. Overview requests /api/dashboard?lite=1.
5. Dashboard HTTP requests no longer attach to/evaluate the Indeed page.
   Chrome/Indeed checking remains owned by the background monitor.
6. Candidate rows are fetched only when Candidates is opened.
7. Sent history is fetched only when Sent Responses is opened.
8. Activity logs are fetched only when Activity is opened.
9. Previous lightweight dashboard state is cached in browser localStorage so
   repeat opens can show the last known status immediately while the backend
   reconnects.

Recruitment, Gmail, duplicate protection, live applicant detection, role
verification, automatic role ranking and AI shortlist logic are unchanged.


V11.10.2 - FULL RECOVERY / SETTINGS / ROLE REVIEW FIX
------------------------------------------------------

This release fixes the production problems visible in V11.10.1:

1. ROLE REVIEW HTTP 500 AFTER V11.9 UPGRADE
   Older V11.9 candidate_reviews tables did not contain rank_position and
   auto_shortlisted. V11.10.1 attempted to create indexes on those columns
   before ALTER TABLE added them. V11.10.2 migrates columns first, indexes last.

2. OVERVIEW HTTP 500
   Dashboard components now fail independently. One damaged subsystem no longer
   makes candidates, settings and sent history look like they all disappeared.
   The overview returns a partial healthy payload while the affected component
   retries.

3. SAVED SETTINGS NOT SHOWING
   Backend settings are now the source of truth after connection. Browser cache
   is only a fast startup preview. Settings and Messaging reload the persistent
   settings file when opened.

4. MESSAGE EDITOR SAVE
   Messaging now has its own Save message button. Blank old templates are
   automatically restored to the safe default acknowledgement template.

5. FAST SETTINGS SAVE
   Save is local/atomic and returns immediately. Gmail network verification runs
   in the background so SMTP latency cannot make Save appear broken.

6. DATA SAFETY
   Before V11.10.2 schema migration, the persistent SQLite database is backed up
   once to:
       %LOCALAPPDATA%\NunesRecruitmentConsole\backups\automation_pre_v11_10_2.db

7. SAVED DATA STATUS
   /api/data-status reports the persistent database path/counts, Gmail saved
   state and schema state for diagnostics.

Existing thank-you sending, duplicate protection, live monitoring and automatic
role ranking remain unchanged.


V11.10.3 - CHROME CDP SESSION RECOVERY + IMMEDIATE SEND WAKE
-------------------------------------------------------------

FIXED FROM LIVE CANDIDATE SCREEN
--------------------------------
Observed error:
  Candidate page failed: Chrome CDP Page.navigate failed:
  code -32001 / Session with given id not found

V11.10.3 tracks each flattened CDP page session back to its Chrome target.

When Chrome/Indeed invalidates a page-level session:
  1. browser-level DevTools connection remains open
  2. the console finds the existing/replacement Indeed target
  3. it attaches a fresh page session
  4. aliases the old session id to the new one
  5. retries the failed Page.navigate / Runtime.evaluate command once
  6. continues extracting the SAME candidate

This avoids converting every applicant into:
  Detecting role
  Detecting verified email
  Candidate page failed

START AUTOMATION RESPONSE
-------------------------
Start Automation now wakes:
  - Chrome/Indeed live candidate scanner immediately
  - Gmail outbox immediately

After a candidate becomes:
  application verified + real email + exact role

the scanner wakes the mail outbox immediately rather than waiting for the next
normal polling boundary.

The one-second values remain safety fallbacks, not intentional delays.

IMPORTANT
---------
If the dashboard says "Automation off", no automatic scanning or email sending
will occur until Start Automation is pressed. Stop Automation remains a real
persistent stop control.

No frontend source was changed in V11.10.3, so an existing compatible
V11.10.2/V11.10.1 dashboard build can be reused without another UI rebuild.


V11.10.4 - LIVE TODAY RECOVERY
-------------------------------
31 Aug remains the permanent First detected date for historical candidates.

New:
  Live Today = applicants actually present in a successful scan on today's
  Windows local date.
  History = all older + current records.

Candidates now shows both:
  First detected
  Last seen

Automation ON actively repairs Chrome/Indeed Candidates binding instead of
sitting in Starting when the saved Candidates URL is missing/stale.

Pressing Start Automation clears the old success timestamp. The UI stays
Starting until a NEW successful scan finishes. A successful scan with 0 new
applicants is still considered LIVE.


V11.10.5 - LIVE / SETTINGS / RANKING RECOVERY
----------------------------------------------
Fixed:
  name 'get_state' is not defined
  ReferenceError: scopeRoot is not defined

New/current applicants are ranked first.
Completed unchanged resumes are skipped.
Candidates whose Indeed workflow status is Hired, Selected, Not Selected,
Rejected, Withdrawn or Archived are removed from active ranking but retained
in history.

Mail delivery has priority over ranking. Ranking runs in its own worker so it
cannot intentionally delay a verified acknowledgement.


V11.10.6 - LIVE ENGINE RECOVERY / START API FIX
------------------------------------------------
CONFIRMED V11.10.5 RUNTIME BUG FIXED:
  app.py used datetime.now(timezone.utc) in the live worker and Start endpoint
  without importing datetime/timezone. That can make Start return Internal
  backend error and prevents the live heartbeat from running.

V11.10.6 imports the clock correctly and executes a local runtime self-test
before background workers start.

START AUTOMATION:
  - saves ON immediately
  - wakes scanner/outbox/ranking
  - returns to UI immediately
  - Chrome/Indeed reconnect runs in a background thread
  - browser/CDP failures cannot make the Start API itself return HTTP 500

LIVE RECOVERY:
  - repeated dead-session / Page.navigate / candidate-page failures clear only
    the stale saved Candidates binding
  - existing Chrome is rebound automatically
  - fast candidate collector falls back once to the independent full collector
    if its JavaScript fails

RANKING:
  - New/current applicants remain highest priority
  - candidates with Indeed status Hired / Selected / Not Selected / Rejected /
    Withdrawn / Archived are removed from ACTIVE ranking but kept in history
  - all other active candidates continue role-vs-resume ranking
  - completed unchanged rankings are skipped until resume/job description changes

DIAGNOSTICS:
  DIAGNOSE_V11_10_6.bat calls /api/self-test and prints live connection state.


V11.10.7 - SENT RESPONSES MODERN UI ONLY
------------------------------------------
Only the Sent Responses frontend view was redesigned.

Changed:
  - delivery summary cards
  - modern search control
  - improved table spacing and column widths
  - clearer Delivered / correction badges
  - compact candidate identity presentation
  - cleaner applied-role display
  - modern expandable response preview
  - larger fixed internal history scroll area
  - responsive layout

Unchanged:
  live Indeed monitoring
  Gmail sending
  duplicate protection
  candidate extraction
  role ranking
  database schema/data
  all other application pages


V11.10.8 - WINDOWS COMPATIBILITY + FASTER INSTALL/OPEN
-------------------------------------------------------

NO RECRUITMENT LOGIC OR SENT-RESPONSES UI WAS CHANGED IN THIS RELEASE.

WINDOWS SUPPORT
---------------
Designed/tested setup path for:
  Windows 10 x64
  Windows 11 x64
  Windows Server 2019 / 2022 / 2025 x64

It can also run on another Windows release when that Windows version can run
the required compatible Python/Node runtimes, but obsolete/EOL Windows versions
cannot be guaranteed.

PYTHON
------
The launcher no longer requires exactly Python 3.12.

Compatible:
  Python 3.10
  Python 3.11
  Python 3.12
  Python 3.13

If one of those already has all required packages, it is used directly and the
virtual-environment install is skipped.

If Python is completely missing and Windows has winget, START.bat installs
Python 3.12 once.

NODE
----
Node.js 20.9 or newer is accepted. Current Node 22/24/26 installations work.

Node is searched in PATH and common Windows installation locations. If it is
missing and winget is available, Node.js LTS is installed once.

FAST INSTALL / FAST OPEN
------------------------
The setup reuses, in this order:
  1. already-running V11.10.8
  2. compatible cached V11.10.7 runtime
  3. existing system Python packages
  4. shared Python cache
  5. previous V11.x Python environment
  6. previous/shared node_modules
  7. previous compatible Next.js production build

Only the missing piece is installed.

A compatible V11.10.7 cache is accepted directly because this release changes
only Windows startup/setup code; the dashboard source/dependencies are the same.

DAILY START
-----------
Daily START.bat does not run:
  pip install
  npm install
  Next.js build

when the cache is ready.

FIRST NEW PC
------------
The first run can still take longer only when that PC genuinely has no reusable
Python packages, Node packages or dashboard build. Subsequent starts use the
cached runtime.

AUTO START
----------
Windows Task Scheduler startup delay is reduced from 10 seconds to 5 seconds.

UI BROWSER
----------
The local dashboard can open with Chrome, Microsoft Edge, or the Windows
default browser. Indeed automation still uses the configured Chrome remote
debugging workflow.


V11.10.9 - NAS / UNC SAFE STARTUP
----------------------------------

PROBLEM FIXED
-------------
Launching a BAT file directly from a UNC path such as:

  \\Nunes_Nas\Common\Sivanesan\NUNES_...

can produce:

  CMD.EXE was started with the above path as the current directory.
  UNC paths are not supported. Defaulting to Windows directory.

The old launcher then executed from C:\Windows and tried to create:

  C:\Windows\.venv

which fails with WinError 5 / Access is denied.

NEW BEHAVIOR
------------
When START.bat detects a \\server\share path, it does NOT run Python/Node from
the NAS.

It copies only the small application files once to:

  %LOCALAPPDATA%\NunesRecruitmentConsole\AppCache\V11_10_9

It excludes:
  .venv
  node_modules
  .next
  database/log/PID files

Then the application installs/starts from the LOCAL cache.

Benefits:
  - no C:\Windows\.venv error
  - no CMD UNC-current-directory error
  - much faster Python/Next.js work
  - npm/Next build is never performed over the NAS
  - Windows auto-start points to the local cached application
  - after the local cache exists, the app can start even if the NAS is
    temporarily unavailable

DATA
----
Persistent candidate/settings/history data remains in the existing
%LOCALAPPDATA%\NunesRecruitmentConsole location. No recruitment data is reset.

UPDATE BEHAVIOR
---------------
Each new version gets a separate local AppCache folder. The first run of that
new version copies the small source files once. Later starts skip that copy.

RECRUITMENT LOGIC
-----------------
V11.10.9 changes only startup/NAS compatibility. Candidate extraction, Gmail,
live monitoring, ranking, database logic and Sent Responses UI remain unchanged.


V11.11.1 - MULTI-ROLE LIVE RECRUITMENT PIPELINE
------------------------------------------------
CONNECTED INDEED EMPLOYER ACCOUNT
---------------------------------
The application continues using the already-approved Chrome/Indeed Employer
session. It does not require a separate new Indeed login for every role.

ALL CURRENT ROLES
-----------------
The Jobs screen is discovered automatically. Every detected role gets its own
ranking list. New roles are added automatically; paused/closed roles are frozen.

The existing roles already stored in candidate history are also seeded into the
role workspace, so the first V11.11.1 run can begin processing the existing
multi-role backlog while Indeed job descriptions are synchronized.

NEW APPLICATION FLOW
--------------------
  New applicant arrives on Indeed
      -> exact application / role / resume / contact verification
      -> existing duplicate-safe thank-you EMAIL flow
      -> role-specific thank-you WHATSAPP flow
      -> resume vs official Indeed job description analysis
      -> role ranking recalculated
      -> changed ranking report queued separately for that role

The acknowledgement message is role-aware and tells the applicant the
recruitment team will contact them regarding the next steps.

CONTINUOUS RANKING
------------------
Ranking is recalculated by job-related resume evidence score.

Example:
  Applicant A applies today and is Rank #1.
  Applicant B applies tomorrow with stronger job-description evidence.
  Applicant B becomes Rank #1 and Applicant A moves to Rank #2.

New/current applicants are analyzed first. Completed unchanged resume/job
analysis is reused instead of repeatedly re-reading the same file.

ROLE-WISE EMAIL REPORTS
-----------------------
Ranking reports are independent for every role.

Fixed sender:
  nunescbe@gmail.com

Default recipient:
  nunescbe@gmail.com

Example subjects:
  Recruitment Ranking - Purchase Executive
  Recruitment Ranking - Marketing & Lead Coordination Executive

A new report is queued only when that role's ranking snapshot changes. An older
unsent snapshot is superseded instead of sending stale rankings.

The Google App Password for nunescbe@gmail.com is stored separately from the
candidate-acknowledgement Gmail credential.

WHATSAPP
--------
WhatsApp Web uses the existing approved Chrome browser. A one-time QR login may
be required on a new PC/browser profile. After that, role-specific applicant
acknowledgements and HR-approved interview messages use the saved WhatsApp Web
session.

HR APPROVAL GATE
----------------
Automatic ranking is a review aid. It does not send an interview invitation by
itself.

HR selects a ranked candidate and clicks:
  Approve next-working-day interview

Only after that approval:
  interview email is queued
  interview WhatsApp is queued
  the scheduled date is the next Monday-Friday date

Example:
  approval Friday -> interview date Monday

ROLE CLOSED / PAUSED
--------------------
When Indeed marks a role Paused or Closed:
  active ranking is frozen
  new applicant outreach is blocked
  queued acknowledgement/interview outreach for that role is paused
  HR cannot approve a new interview for that role
  historical candidates/ranking remain visible

A CLOSED role may generate one final role ranking report for history.

TERMINAL CANDIDATE STATUS
-------------------------
Candidates already marked by the workflow as:
  Hired
  Selected
  Not Selected
  Rejected
  Withdrawn
  Archived

are removed from the ACTIVE ranking list but retained in history.

RANKING SAFETY
--------------
Ranking uses job-related requirements and resume evidence. Protected/sensitive
personal attributes such as gender/sex, age, caste, religion, race/ethnicity,
disability/health, sexual orientation and political/union information are not
used in the ranking score.

FAST/NAS STARTUP
----------------
V11.10.9 NAS/UNC-safe local caching remains. V11.11.1 still runs Python/Node and
Next.js locally instead of building on a NAS path. Existing Python packages and
node_modules are reused when compatible.

V11.11.1 - TOP-BAR RECRUITMENT OPERATIONS HUB
----------------------------------------------

FRONTEND DESIGN
---------------
The left sidebar has been removed. Navigation is now a modern sticky top bar:
  Overview | Candidates | Roles | Sent | Messaging | Activity | Settings

The top bar also contains:
  - global candidate search
  - system attention indicator
  - Start Automation / Stop Automation
  - local workspace/version badge

OVERVIEW
--------
The Overview page now matches the approved Recruitment Operations Hub concept:
  - Hiring Pipeline
      New Applicants -> Acknowledged -> Ranked -> HR Review -> Interview -> Completed
  - Active Roles with per-role live pipeline counts
  - AI Ranking Status
  - Live Indeed Detection
  - Candidate Processing

NO DEMO/FIXED COUNTS
--------------------
All overview counts are wired to real backend data. V11.11.1 adds a lightweight
read-only endpoint:

  GET /api/recruitment/overview

It returns role-level pipeline counts and recent candidate processing status in
one request. This avoids loading every full candidate/role payload on the
Overview page and keeps the Next.js dashboard fast.

ROLE WIRING
-----------
Clicking "View role" stores the selected role for the Role Review screen. Role
Review opens that exact role instead of always jumping to the first role.

SEARCH WIRING
-------------
The top search box routes directly to Candidates and applies the search text to
the real candidate history.

AUTOMATION WIRING
-----------------
Top-bar buttons use the existing production endpoints:
  POST /api/automation/start
  POST /api/automation/stop

Live Indeed buttons use:
  POST /api/open-indeed
  POST /api/connect

DATA / WORKFLOW SAFETY
----------------------
Candidate detection, Gmail delivery, WhatsApp delivery, duplicate protection,
AI ranking, HR approval, database paths and historical data are unchanged.

FIRST START OF V11.11.1
-----------------------
The frontend source changed, so V11.11.1 performs one new Next.js production
build. Existing Python packages and compatible node_modules are still reused.
After that first build, normal START.bat uses the fast cached runtime.


V11.11.2 - ALWAYS-ON HR BATCH INTERVIEW WORKFLOW
-------------------------------------------------
The application now runs recruitment automation continuously while START.bat/service is running. The frontend Start/Stop buttons were removed. Use STOP.bat only when the entire local service must be stopped.

WhatsApp Web is opened automatically in the same approved Chrome profile. If the account has never been linked on that Chrome profile, scan the QR once. The saved browser session is reused afterwards.

Role Review now contains an HR Interview Selection control. HR can choose Top 20, Top 30, Top 40 or a custom count. The AI ranking remains live as new applicants arrive; the interview batch is released only after the HR action.

Interview scheduling rule: approvals Monday-Wednesday are scheduled for the next day. Approvals Thursday-Friday are scheduled for Monday. The second-stage email and WhatsApp messages are queued immediately after HR approval and include the scheduled interview date.

Messaging now includes editable Stage 1 acknowledgement email/WhatsApp templates and Stage 2 interview email/WhatsApp templates.


V11.11.3 - LARGE UI + INSTANT DUAL ACK + SINGLE EOD REPORT
-----------------------------------------------------------
Stage 1: when a new/recent application is verified from the resume/application, Gmail acknowledgement and WhatsApp acknowledgement are released independently. If both contacts exist, both are sent.

Ranking: every active applicant remains in the role ranking. A late stronger applicant can move to rank #1. Before HR approval, the top list explicitly shows Stage 2 as NOT SENT. HR may press the same Top N approval again after late applicants arrive; already-approved candidates are not duplicated and only newly eligible top-N candidates are added.

Stage 2: interview email + WhatsApp templates are editable under Messaging. HR approval remains mandatory.

End of day: the old automatic separate role-report mails are no longer sent continuously. One consolidated email is queued from nunescbe@gmail.com at the configured Windows-local time (default 19:00). The email contains every ongoing role as a separate section and each role's current candidate/rank/contact/Stage-1/HR/Stage-2 status. Paused/closed roles are excluded.

V11.11.4 - STRICT AI RANKING + NO DUPLICATE ACK + GITHUB LIVE UPDATE
---------------------------------------------------------------------
See V11_11_4_CHANGE_STATUS.txt.

IMPORTANT OPENAI KEY SAFETY
---------------------------
Never paste an OpenAI API key into source files or settings.json. Use Settings ->
Strict AI Ranking or SET_OPENAI_RANKING_KEY.bat. The Windows build encrypts the
key with the current Windows user's DPAPI and uses it only for role ranking.

RANKING POLICY
--------------
1. Parse official Indeed job requirements.
2. Compare resume evidence locally first.
3. Weak matches remain local only.
4. Very clear high matches remain local only.
5. Only mid-range evidence is optionally refined by the low-cost OpenAI model.
6. AI has a small score weight and cannot freely replace the deterministic score.
7. Scores are capped below 100 to avoid false certainty.
8. Equal scores are ordered by earliest application first.
9. If the daily API cap is reached, ranking continues locally.

GITHUB
------
Run GITHUB_SETUP_ONCE.bat after creating/choosing the intended GitHub repository.
The repository must contain code only; candidate records, resumes, Gmail passwords,
OpenAI key, .next, node_modules and local settings are excluded. After setup,
PUSH_UPDATE_TO_GITHUB.bat publishes future code changes. Running clients check
origin/main every 60 seconds, pull/rebuild/restart automatically, then the browser
can be refreshed to see the new version.


V11.11.5 - SHADCN UI + FIXED GITHUB REPOSITORY
------------------------------------------------
Repository:
  https://github.com/Nunes-instruments/indeed_auomation.git

UI:
- Shared shadcn-style Button system for navigation and application actions.
- Clear default / outline / secondary / ghost / destructive / success /
  warning / subtle variants.
- Shared Input, Textarea, Select and Badge primitives.
- Medium readable control sizes and stronger keyboard focus states.
- Consistent disabled/loading behavior.
- Existing workflow/data/backend behavior remains unchanged.

GITHUB:
- GITHUB_SETUP_ONCE.bat is preconfigured for the repository above.
- No repository URL prompt is required.
- PUSH_UPDATE_TO_GITHUB.bat pushes future code updates to main.
- Running installations keep checking GitHub every 60 seconds.
- Local data/secrets/builds remain excluded through .gitignore.
