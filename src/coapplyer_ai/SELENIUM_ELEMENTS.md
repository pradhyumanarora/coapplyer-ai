# LinkedIn Easy Apply — Element Reference for Selenium

This document lists **every element** clicked, typed into, or read during the Playwright-MCP
automation runs, translated into **Selenium 4 (Python)** locators. Use it to build a Selenium
auto-applier.

There are **TWO distinct DOMs** on LinkedIn Easy Apply:

1. **Classic modal** — normal light DOM. Standard `By.ID` / `By.CSS_SELECTOR` works.
2. **SDUI flow** — the Apply dialog and ALL its fields live inside **nested shadow roots**.
   `driver.find_element(By.ID, ...)` and `document.getElementById(...)` **DO NOT** reach them.
   You MUST walk shadow roots (helper provided below).

Detect which one you're in: after clicking Easy Apply, look for a light-DOM
`div[role="dialog"]`. If it's `None` but the button existed, you're in the SDUI flow.

---

## 0. Shadow-DOM helpers (REQUIRED for SDUI) — Selenium Python

Selenium's native `element.shadow_root` only descends one level and needs CSS (no `:id` with
colons). The robust, battle-tested approach is a JS recursive walker via `execute_script`,
mirroring exactly what worked in Playwright.

```python
from selenium import webdriver
from selenium.webdriver.common.by import By

WALKER = """
function* walk(root){
  for(const el of root.querySelectorAll('*')){
    yield el;
    if(el.shadowRoot) yield* walk(el.shadowRoot);
  }
}
"""

def sdui_find_dialog(driver):
    """Return True if an SDUI dialog is present (in shadow DOM)."""
    return driver.execute_script(WALKER + """
      for(const el of walk(document)){
        if(el.getAttribute && el.getAttribute('role')==='dialog') return true;
      }
      return false;
    """)

def sdui_read_fields(driver):
    """Return list of {tag,type,id,value,checked,label,options} for every field in dialog."""
    return driver.execute_script(WALKER + """
      let dlg=null;
      for(const el of walk(document)){ if(el.getAttribute&&el.getAttribute('role')==='dialog'){dlg=el;break;} }
      if(!dlg) return [];
      const all=[...walk(dlg)];
      const out=[];
      for(const el of all){
        const t=el.tagName.toLowerCase();
        if(t==='input'||t==='select'||t==='textarea'){
          let label='';
          if(el.id){const l=all.find(x=>x.tagName==='LABEL'&&x.getAttribute('for')===el.id); if(l)label=l.innerText.trim();}
          const o={tag:t,type:el.type||'',id:el.id||'',value:el.value||'',checked:el.checked||false,label:label};
          if(t==='select') o.options=[...el.options].map(op=>op.text);
          out.push(o);
        }
      }
      return out;
    """)

def sdui_list_buttons(driver):
    """Return aria-label/innerText of every BUTTON in the dialog."""
    return driver.execute_script(WALKER + """
      let dlg=null;
      for(const el of walk(document)){ if(el.getAttribute&&el.getAttribute('role')==='dialog'){dlg=el;break;} }
      if(!dlg) return [];
      return [...walk(dlg)].filter(e=>e.tagName==='BUTTON')
        .map(b=>b.getAttribute('aria-label')||b.innerText.trim()).filter(Boolean);
    """)

def sdui_set_input(driver, field_id, value):
    """Set a text/numeric input by id (inside shadow DOM). Returns read-back value."""
    return driver.execute_script(WALKER + """
      const all=[...walk(document)];
      const el=all.find(e=>e.id===arguments[0]);
      if(!el) return 'missing';
      el.value='';                          el.dispatchEvent(new Event('input',{bubbles:true}));
      el.value=arguments[1];                el.dispatchEvent(new Event('input',{bubbles:true}));
                                            el.dispatchEvent(new Event('change',{bubbles:true}));
      return all.find(e=>e.id===arguments[0]).value;
    """, field_id, value)

def sdui_set_select(driver, field_id, visible_text):
    """Select an <option> by visible text (inside shadow DOM). Returns read-back text."""
    return driver.execute_script(WALKER + """
      const all=[...walk(document)];
      const el=all.find(e=>e.id===arguments[0]);
      if(!el) return 'missing';
      const o=[...el.options].find(x=>x.text.trim()===arguments[1]);
      if(!o) return 'noopt';
      el.value=o.value; el.dispatchEvent(new Event('input',{bubbles:true}));
                        el.dispatchEvent(new Event('change',{bubbles:true}));
      return all.find(e=>e.id===arguments[0]).selectedOptions[0].text;
    """, field_id, visible_text)

def sdui_click_button(driver, regex):
    """Click first dialog BUTTON whose aria-label/text matches regex (JS RegExp source)."""
    return driver.execute_script(WALKER + """
      let dlg=null;
      for(const el of walk(document)){ if(el.getAttribute&&el.getAttribute('role')==='dialog'){dlg=el;break;} }
      if(!dlg) return 'nodialog';
      const re=new RegExp(arguments[0],'i');
      const b=[...walk(dlg)].filter(e=>e.tagName==='BUTTON')
        .find(x=>re.test(x.getAttribute('aria-label')||'')||re.test(x.innerText||''));
      if(!b) return 'notfound';
      b.click(); return 'clicked';
    """, regex)

def sdui_click_checkbox_by_label(driver, label_substr, want_checked):
    """Check/uncheck a checkbox/radio located via its label text (shadow DOM)."""
    return driver.execute_script(WALKER + """
      const all=[...walk(document)];
      const lbl=all.find(x=>x.tagName==='LABEL'&&(x.innerText||'').includes(arguments[0]));
      let box=null;
      if(lbl){ const fid=lbl.getAttribute('for'); box=all.find(e=>e.id===fid); }
      if(!box) return 'missing';
      if(box.checked!==arguments[1]){ box.click(); }
      return all.find(e=>e.id===box.id).checked;
    """, label_substr, want_checked)
```

> **CRITICAL fill rule (both DOMs):** never batch-set multiple controlled inputs in one shot
> without read-back. Set ONE field, then re-read its `.value` to confirm React kept it. The
> `value=''`+`input` clear-then-set-then-`change` sequence is required or React reverts it.

---

## 1. Opening the application

### 1a. Easy Apply trigger — TWO variants
| Variant | Selenium locator |
|---|---|
| Classic button | `driver.find_element(By.CSS_SELECTOR, "button.jobs-apply-button")` — also matched by `By.XPATH, "//button[contains(@aria-label,'Easy Apply')]"` |
| SDUI link (`<a>`) | `driver.find_element(By.CSS_SELECTOR, "a[href*='openSDUIApplyFlow=true']")` — or `By.XPATH, "//a[@aria-label='Easy Apply to this job']"` |

Try the button first; if absent, click the link. After clicking, wait ~2 s (dialog appears
after a delay), then branch classic vs SDUI.

### Job page URL pattern
`https://www.linkedin.com/jobs/view/<JOB_ID>/`  — `JOB_ID` is the numeric id (e.g. `4440465288`).

---

## 2. Field ID pattern (applies to BOTH classic & SDUI)

All form-field ids follow this template:

```
<component>-formElement-urn-li-jobs-applyformcommon-easyApplyFormElement-<JOB_ID>-<QUESTION_ID>-<suffix>
```

| Field kind | `<component>` prefix | `<suffix>` |
|---|---|---|
| Single-line text / numeric | `single-line-text-form-component` | `-numeric` (numeric) or none (text) |
| Dropdown (single choice) | `text-entity-list-form-component` | `-multipleChoice` |
| Phone national number (classic) | `single-line-text-form-component` | `-phoneNumber-nationalNumber` |
| Phone country (classic) | — | `-country` |

- `<QUESTION_ID>` is a numeric urn (e.g. `21413196289`) — different per question, **stable per job**.
- **IDs contain no colons in the SDUI numeric variant** (dash-separated), but classic phone ids
  can. When an id contains a colon, CSS `#id` breaks — use `By.ID` (Selenium escapes it) or the
  JS `getElementById`/walker path.

**Selenium light-DOM (classic):** `driver.find_element(By.ID, field_id)`
**Selenium SDUI:** use `sdui_set_input` / `sdui_set_select` above (By.ID will NOT find it).

---

## 3. Concrete elements interacted with, per job

### Step buttons (SDUI dialog, by aria-label / text) — click via `sdui_click_button`
| Purpose | Regex to match |
|---|---|
| Advance a step | `Continue to next step` |
| Go to review | `Review your application` |
| Final submit | `Submit application` |
| Go back | `Back to previous step` |
| Close dialog / confirmation | `Dismiss` |
| Reveal more resumes | `Show \d+ more resumes` |

Classic modal uses the **same aria-labels** but as light-DOM buttons:
`driver.find_element(By.XPATH, "//button[@aria-label='Submit application']")` etc.

### 3a. DAZN — Node.JS Software Engineer (JOB_ID 4440465288) — SDUI multi-step
Steps: contact → resume → top-choice → **Additional Questions** → review → submit.

**Contact step (pre-filled, verify only):**
- Email select — `select` (select-one), option text like `pradhyumanarora@gmail.com`.
- Phone country select — options are full text `India (+91)`; select by visible text.
- Phone national number — text input.

**Resume step (radios):**
- Selected resume radio label: `Deselect resume <filename>` (checked = true) — leave as-is.
- Alternate radio label: `Select resume <filename>` (checked = false).
- Default already-selected resume is fine → just Continue.

**Top-choice step:**
- A checkbox — **LEAVE UNCHECKED**. (Locate via label containing "top choice".)

**Additional Questions step — fields actually typed into (each rendered TWICE; fill both):**
| Label | QUESTION_ID (copy 1 / copy 2) | Full id template | Value entered |
|---|---|---|---|
| Can we know your current compensation? | `21413196289` / `21413196393` | `single-line-text-form-component-formElement-urn-li-jobs-applyformcommon-easyApplyFormElement-4440465288-<QID>-numeric` | `50` |
| Can we know your expected compensation? | `21413196297` / `21413196385` | same template | `70` |
| What's your notice period? | `21413196281` / `21413196377` | same template | `60` |
| Are you willing to work from office? all 5days | `21413196273` / `21413196369` | `text-entity-list-form-component-formElement-urn-li-jobs-applyformcommon-easyApplyFormElement-4440465288-<QID>-multipleChoice` | select `Yes` |

> **SDUI double-render gotcha:** the dialog often contains TWO copies of the same field
> (different QUESTION_IDs). Fill **all** matching ids or validation may fail on the hidden copy.

### 3b. Teradata — Software Engineer (JOB_ID 4326661453) — SDUI single-page
- Single-page: after opening, `Submit application` button present immediately (no Continue).
- Same id template with `JOB_ID=4326661453`.

### 3c. Classic modal jobs (PDI 4407312731, Deloitte 4442311710, Charles Schwab 4445340586)
- Light DOM. `driver.find_element(By.ID, ...)` works directly.
- Numeric answer id: `...-<JOB_ID>-<QID>-numeric`
- Dropdown id: `...-<JOB_ID>-<QID>-multipleChoice`
- Phone national: `...-<JOB_ID>-<QID>-phoneNumber-nationalNumber`
- Phone country: `...-<JOB_ID>-<QID>-country`
- Nav buttons by aria-label: `Continue to next step`, `Review your application`,
  `Submit application`, `Back to previous step`, `Dismiss`.

### 3d. MathWorks — Software Engineer (JOB_ID 4431071241) — SDUI multi-step
Steps: contact → resume → top-choice(optional, leave unchecked) → Additional Qs → review → submit.
| Label | QUESTION_ID | Kind | Value |
|---|---|---|---|
| Do you have a Bachelor's degree? | `21150194897` | RADIO (`-0`=Yes) | Yes |

### 3e. Turing — Remote Software Engineer / Go (JOB_ID 4444486366) — SDUI 5-step
| Label | QUESTION_ID | Kind | Value |
|---|---|---|---|
| Email | `34554558738` | select | pradhyumanarora@gmail.com |
| Phone national number | `34554558722` | text | 8439803020 |
| Resume radio | `34554558714` | radio | keep default selected |
| Privacy consent / agree | `34554558730` | SELECT `-multipleChoice` | `Yes` |

### 3f. Kore.ai — Full Stack Engineer (JOB_ID 4432365959) — SDUI 4-step
| Label | QUESTION_ID | Kind | Value |
|---|---|---|---|
| Email | `21183989185` | select | pradhyumanarora@gmail.com |
| Phone national number | `21183989177` | text | 8439803020 |
| Resume radio | `21183989169` | radio | keep default selected |
| Do you have a degree / education | `21183989201` | RADIO (`-0`=Yes) | Yes |
| Years of Node.js experience | `21183989193` | numeric | `1` |

### 3g. Endowus — Senior Software Engineer, Backend (JOB_ID 4436492838) — SDUI 5-step
| Label | QUESTION_ID | Kind | Value |
|---|---|---|---|
| Resume radio | `33866398362` | radio | keep default selected |
| Years of Scala experience | `33866398354` | numeric | `0` |
| Years of Java experience | `33866398346` | numeric | `2` |
| Years of JavaScript experience | `33866398394` | numeric | `2` |
| Willing to work from Hyderabad office | `33866398386` | select `-multipleChoice` | `Yes` |

> **Skill-years heuristic:** if resume lacks the skill (e.g. Scala) answer `0`; otherwise ~`2`.

### 3h. Freshworks — Senior Software Engineer, Full Stack (JOB_ID 4434221716) — SDUI 7-step + consent
**Full step order & progress %:** Contact (~14%) → Resume (29%) → Top-choice Optional (~29%)
→ Work experience (43%) → Education (57%) → Additional Qs (71%) → **Privacy consent (86%)** →
Review (100%). Prefilled Work-exp & Education steps have NO fillable fields — just Continue.

**Contact info QIDs:**
| Label | QUESTION_ID | Kind |
|---|---|---|
| First name | `33668783146` | text |
| Last name | `33668783170` | text |
| Phone national number | `33668783138` | text (country select same QID = India +91) |
| Location (city) | `33668783154` | **GEO-LOCATION typeahead** (see §3i) |
| Email | `33668783162` | select `-multipleChoice` |

**Additional Questions QIDs (VERIFIED values):**
| Label | QUESTION_ID | Kind | Value |
|---|---|---|---|
| Years of experience | `33668783914` | select | `1-3 years` |
| Last drawn CTC | `33668783898` | text | `50 LPA` |
| Last drawn RSU | `33668783970` | text | `0` |
| Nationality | `33668783962` | text | `Indian` |
| Citizenship | `33668783954` | text | `Indian` |
| Valid work permit? | `33668783946` | RADIO | Yes |
| Previously employed at Freshworks? | `33668783938` | RADIO | No |
| Gender | `33668783994` | select | `Male` |
| (2 optional textareas "0/1,440") | — | textarea | left blank |

**Privacy consent step (86%) — REQUIRED checkbox:**
- `INPUT[type=checkbox]`, id contains `33668784018`
  (full id ends `...easyApplyFormElement:(4434221716,33668784018,multipleChoice)-0`).
- Label: "I consent" / "You declare that you have read and agree to the privacy notice". Shows "Required".
- Must `.click()` if not already checked, then click "Review your application".
- Radio/checkbox id format: `...easyApplyFormElement:(<JOB_ID>,<QID>,multipleChoice)-0|-1` (colon+parens).

### 3i. GEO-LOCATION typeahead (city/location fields) — special handling
Field id ends `-location-GEO-LOCATION`. A synthetic native `value` set does **NOT** trigger the
async suggestion fetch, and Continue then fails with "Required / Select an option". You MUST
simulate real typing and click a suggestion:

```python
def sdui_fill_geo(driver, field_id, city_text, pick_regex):
    # 1. focus the input inside shadow DOM
    driver.execute_script(WALKER + """
      const el=[...walk(document)].find(e=>e.id===arguments[0]); if(el){el.focus();}
    """, field_id)
    # 2. type with real key events (ActionChains types into the focused element)
    from selenium.webdriver.common.action_chains import ActionChains
    ActionChains(driver).send_keys(city_text).perform()   # ~120ms/char is safest
    time.sleep(2.5)                                        # wait for async suggestions
    # 3. click the matching role=option (ids look like basic-result-XXXX)
    return driver.execute_script(WALKER + """
      const re=new RegExp(arguments[0],'i');
      const opt=[...walk(document)]
        .filter(e=>e.getAttribute&&e.getAttribute('role')==='option')
        .find(o=>re.test(o.innerText||''));
      if(!opt) return 'nooption';
      opt.click(); return 'picked:'+opt.innerText.trim();
    """, pick_regex)
```
- For Hyderabad: `city_text="Hyderabad"`, `pick_regex="Hyderabad, Telangana, India"`.
- Suggestion nodes have `role="option"` and ids like `basic-result-1234`.

---

## 4. Common screening answers used (data to feed the script)

| Question pattern | Answer |
|---|---|
| Current compensation | `50` (LPA) |
| Expected compensation | `70` (LPA) |
| Notice period (days) | `60` |
| Willing to WFO all 5 days | `Yes` |
| Total experience (yrs) | `2` |
| Relevant experience (yrs) | `2` |
| Willing to relocate | `Yes` (anywhere in India) |
| "Are you currently in <City>?" (city ≠ Hyderabad) | `No` |
| Work authorization / sponsorship | Indian citizen, no sponsorship needed |
| Phone | national `8439803020`, country `India (+91)` |
| Email | `pradhyumanarora@gmail.com` |

---

## 5. Success detection

**Classic:** URL navigates to `/jobs/search/post-apply/next-best-action/`; page body matches
`/Your application was sent|Application sent/i`.

**SDUI:** URL may NOT change. A confirmation **dialog** (shadow DOM) appears with heading
`Next best action` and body matching `/application was sent/i`. Detect with the walker:

```python
def sdui_success(driver):
    return driver.execute_script(WALKER + """
      for(const el of walk(document)){
        if(/application was sent/i.test(el.innerText||'')) return true;
      }
      return false;
    """)
```

Then dismiss: `sdui_click_button(driver, 'Dismiss')`.

> **Gotcha:** prefer scanning the confirmation **dialog scope** (the new `role=dialog` node),
> not a full-page leaf-text join. A whole-document `innerText` join sometimes returns only
> `<head>`/`<script>` noise and yields a FALSE NEGATIVE. Find the newest `role=dialog` first,
> then test its `innerText` against `/application was sent/i`.

---

## 6. Things that FAILED (avoid in Selenium)

1. `document.getElementById(id)` / `driver.find_element(By.ID, id)` on SDUI fields → **not found**
   (they're in shadow DOM). Must use the recursive walker.
2. `querySelector('div[role="dialog"]')` in light DOM on SDUI → `null`.
3. Batch-setting several controlled inputs without read-back → React reverts values silently.
4. Setting `.value` without dispatching `input` **and** `change` → LinkedIn won't register it.
5. Assuming Easy Apply is always a `<button>` → SDUI uses an `<a href*="openSDUIApplyFlow=true">`.
6. Assuming a single-page form → some (DAZN) are multi-step; enumerate buttons each step and
   only submit when `Submit application` / `Review your application` is present.
7. Console shows ~180–200 benign telemetry errors per action — **ignore**, not a failure signal.

---

## 7. Minimal Selenium driver loop (pseudocode)

```python
driver.get(f"https://www.linkedin.com/jobs/view/{job_id}/")
# open apply
try:
    driver.find_element(By.CSS_SELECTOR, "button.jobs-apply-button").click()
except NoSuchElementException:
    driver.find_element(By.CSS_SELECTOR, "a[href*='openSDUIApplyFlow=true']").click()
time.sleep(2)

sdui = sdui_find_dialog(driver)
while True:
    fields = sdui_read_fields(driver) if sdui else read_light_fields(driver)
    for f in fields:
        answer = decide_answer(f["label"])          # from table §4
        if f["tag"] == "select":
            (sdui_set_select if sdui else set_light_select)(driver, f["id"], answer)
        elif f["type"] not in ("checkbox","radio"):
            if f["value"].strip() != str(answer):   # verify-then-fill
                (sdui_set_input if sdui else set_light_input)(driver, f["id"], answer)
    # leave top-choice checkbox unchecked (do nothing)
    btns = sdui_list_buttons(driver) if sdui else light_buttons(driver)
    if any("Submit application" in b for b in btns):
        (sdui_click_button if sdui else light_click)(driver, "Submit application"); break
    elif any("Review your application" in b for b in btns):
        (sdui_click_button if sdui else light_click)(driver, "Review your application")
    else:
        (sdui_click_button if sdui else light_click)(driver, "Continue to next step")
    time.sleep(1.5)

time.sleep(2)
assert sdui_success(driver)
sdui_click_button(driver, "Dismiss")
```
