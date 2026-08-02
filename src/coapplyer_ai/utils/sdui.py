"""
Shared SDUI (shadow-DOM / web-component) browser interaction utilities.

Provides the ``SduiMixin`` class that any pipeline step can inherit from
to gain access to the full set of SDUI JS helpers without duplicating code.
"""

import time

from src.browser_adapters import BrowserAdapter
from src.logging import logger


class SduiMixin:
    """
    Mixin that provides shadow-DOM walker helpers for LinkedIn's SDUI Easy Apply surface.

    Subclasses must expose ``self.browser`` (BrowserAdapter) and ``self.driver``
    (raw Selenium WebDriver, or None for Playwright MCP path).
    """

    # JS shadow-DOM walker snippet — shared single source of truth
    SDUI_WALKER = """
function* walk(root){
    for(const el of root.querySelectorAll('*')){
        yield el;
        if(el.shadowRoot) yield* walk(el.shadowRoot);
    }
}
"""

    # ------------------------------------------------------------------
    # Dialog detection
    # ------------------------------------------------------------------

    def _sdui_dialog_present(self) -> bool:
        try:
            return bool(self.browser.execute_script(self.SDUI_WALKER + """
                for(const el of walk(document)){
                    if(el.getAttribute && el.getAttribute('role')==='dialog') return true;
                }
                return false;
            """))
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Field introspection
    # ------------------------------------------------------------------

    def _sdui_read_fields(self) -> list:
        return self.browser.execute_script(self.SDUI_WALKER + """
            let dlg=null;
            for(const el of walk(document)){
                if(el.getAttribute&&el.getAttribute('role')==='dialog'){dlg=el;break;}
            }
            if(!dlg) return [];
            const all=[...walk(dlg)];
            const out=[];
            for(const el of all){
                const t=(el.tagName||'').toLowerCase();
                if(t==='input'||t==='select'||t==='textarea'){
                    let label='';
                    if(el.id){
                        const l=all.find(x=>x.tagName==='LABEL'&&x.getAttribute('for')===el.id);
                        if(l) label=(l.innerText||'').trim();
                    }
                    if(!label){
                        const p=el.closest('label');
                        if(p) label=(p.innerText||'').trim();
                    }
                    const o={
                        tag:t,
                        type:el.type||'',
                        id:el.id||'',
                        name:el.name||'',
                        value:el.value||'',
                        checked:!!el.checked,
                        required:!!el.required,
                        label:label,
                    };
                    if(t==='select') o.options=[...el.options].map(op=>op.text);
                    out.push(o);
                }
            }
            return out;
        """)

    def _sdui_list_buttons(self) -> list:
        return self.browser.execute_script(self.SDUI_WALKER + """
            let dlg=null;
            for(const el of walk(document)){
                if(el.getAttribute&&el.getAttribute('role')==='dialog'){dlg=el;break;}
            }
            if(!dlg) return [];
            return [...walk(dlg)]
                .filter(e=>e.tagName==='BUTTON')
                .map(b=>b.getAttribute('aria-label')||b.innerText.trim())
                .filter(Boolean);
        """)

    # ------------------------------------------------------------------
    # Field mutation
    # ------------------------------------------------------------------

    def _sdui_click_button(self, regex: str) -> str:
        return self.browser.execute_script(self.SDUI_WALKER + """
            let dlg=null;
            for(const el of walk(document)){
                if(el.getAttribute&&el.getAttribute('role')==='dialog'){dlg=el;break;}
            }
            if(!dlg) return 'nodialog';
            const re=new RegExp(arguments[0],'i');
            const b=[...walk(dlg)].filter(e=>e.tagName==='BUTTON')
                .find(x=>re.test(x.getAttribute('aria-label')||'')||re.test(x.innerText||''));
            if(!b) return 'notfound';
            b.click();
            return 'clicked';
        """, regex)

    def _sdui_set_input(self, field_id: str, value: str) -> str:
        return self.browser.execute_script(self.SDUI_WALKER + """
            const all=[...walk(document)];
            const el=all.find(e=>e.id===arguments[0]);
            if(!el) return 'missing';
            el.value='';
            el.dispatchEvent(new Event('input',{bubbles:true}));
            el.value=arguments[1];
            el.dispatchEvent(new Event('input',{bubbles:true}));
            el.dispatchEvent(new Event('change',{bubbles:true}));
            const r=all.find(e=>e.id===arguments[0]);
            return r&&r.value!==undefined ? r.value : '';
        """, field_id, value)

    def _sdui_set_select(self, field_id: str, visible_text: str) -> str:
        return self.browser.execute_script(self.SDUI_WALKER + """
            const all=[...walk(document)];
            const el=all.find(e=>e.id===arguments[0]);
            if(!el) return 'missing';
            const o=[...el.options].find(x=>x.text.trim()===arguments[1]);
            if(!o) return 'noopt';
            el.value=o.value;
            el.dispatchEvent(new Event('input',{bubbles:true}));
            el.dispatchEvent(new Event('change',{bubbles:true}));
            const r=all.find(e=>e.id===arguments[0]);
            return r&&r.selectedOptions&&r.selectedOptions.length ? r.selectedOptions[0].text : '';
        """, field_id, visible_text)

    def _sdui_set_checked(self, field_id: str, want_checked: bool) -> bool:
        return bool(self.browser.execute_script(self.SDUI_WALKER + """
            const all=[...walk(document)];
            const el=all.find(e=>e.id===arguments[0]);
            if(!el) return false;
            if(!!el.checked !== !!arguments[1]) el.click();
            const r=all.find(e=>e.id===arguments[0]);
            return !!(r && r.checked);
        """, field_id, want_checked))

    def _sdui_fill_geo(self, field_id: str, city_text: str) -> None:
        logger.debug(f"Filling SDUI GEO field {field_id} with city text: {city_text}")
        self.browser.execute_script(self.SDUI_WALKER + """
            const el=[...walk(document)].find(e=>e.id===arguments[0]);
            if(el) el.focus();
        """, field_id)
        if self.driver is not None:
            from selenium.webdriver import ActionChains as _ActionChains
            _ActionChains(self.driver).send_keys(city_text).perform()
        else:
            self.browser.execute_script(self.SDUI_WALKER + """
                const el=[...walk(document)].find(e=>e.id===arguments[0]);
                if(!el) return;
                el.value = arguments[1];
                el.dispatchEvent(new Event('input', {bubbles:true}));
                el.dispatchEvent(new Event('change', {bubbles:true}));
            """, field_id, city_text)
        time.sleep(2.5)
        pick_result = self.browser.execute_script(self.SDUI_WALKER + """
            const q=(arguments[0]||'').toLowerCase();
            const opts=[...walk(document)].filter(e=>e.getAttribute&&e.getAttribute('role')==='option');
            let pick=opts.find(o=>(o.innerText||'').toLowerCase().includes(q));
            if(!pick) pick=opts[0];
            if(!pick) return 'nooption';
            pick.click();
            return 'picked:' + (pick.innerText||'').trim();
        """, city_text)
        logger.debug(f"SDUI GEO pick result: {pick_result}")