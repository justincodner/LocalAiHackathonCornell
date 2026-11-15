(() => {
    const blocks = [];
    let partial = false;
    let characters = 0;
    let visited = 0;
    const stop = () => characters >= 1_000_000 || visited >= 30_000;
    const clean = value => value?.replace(/\s+/g, " ").trim() || "";
    const hidden = element => {
        if (element.hidden || element.getAttribute("aria-hidden") === "true" ||
            /^(SCRIPT|STYLE|TEMPLATE|NOSCRIPT|HEAD)$/.test(element.tagName)) return true;
        const style = getComputedStyle(element);
        return style.display === "none" || style.visibility === "hidden" ||
            style.visibility === "collapse" || style.contentVisibility === "hidden";
    };
    const emit = (role, depth, text, extra = {}) => {
        characters += text.length + (extra.value?.length || 0) + 1;
        blocks.push({role, depth, text, ...extra});
    };
    const label = element => {
        const ids = clean(element.getAttribute("aria-labelledby")).split(" ");
        const named = clean(ids.map(id => document.getElementById(id)?.textContent || "").join(" "));
        return named || clean(element.getAttribute("aria-label")) ||
            clean(Array.from(element.labels || [], item => item.innerText).join(" ")) ||
            clean(element.getAttribute("title")) || clean(element.getAttribute("placeholder"));
    };

    function walk(element, depth, textDepth) {
        visited += 1;
        if (depth > 100 || stop()) {
            partial = true;
            return;
        }
        if (hidden(element)) return;
        const tag = element.tagName;
        const aria = element.getAttribute("role");
        let value;
        let role;
        if (tag === "INPUT") {
            const type = element.type;
            if (type === "hidden") return;
            if (type === "password") value = "(hidden)";
            else if (type === "checkbox" || type === "radio") value = element.checked ? "on" : "off";
            else if (type === "file") value = Array.from(element.files || [], file => file.name).join(", ");
            else value = element.value;
            role = {checkbox: "checkbox", radio: "radio", range: "slider", button: "button",
                submit: "button", reset: "button", image: "button"}[type] || "textfield";
        } else if (tag === "TEXTAREA") {
            role = "textarea";
            value = element.value;
        } else if (tag === "SELECT") {
            role = "popup";
            value = Array.from(element.selectedOptions, option => option.text).join(", ");
        }
        if (role) {
            emit(role, textDepth, label(element) || clean(element.getAttribute("alt")), {value});
            return;
        }
        if (/^H[1-6]$/.test(tag) || aria === "heading") {
            const level = Number(element.getAttribute("aria-level") || tag.slice(1)) || 2;
            const text = label(element) || clean(element.innerText);
            if (text) emit("heading", textDepth, text, {level});
            return;
        }
        if (tag === "IFRAME" || tag === "CANVAS" || element.shadowRoot) partial = true;
        if (tag === "IMG") {
            const text = label(element) || clean(element.alt);
            if (text) emit("image", textDepth, text);
            return;
        }
        if (tag === "TABLE" || ["table", "grid", "treegrid"].includes(aria)) {
            emit("table", textDepth, label(element) || clean(element.caption?.innerText));
            textDepth += 1;
        } else if (tag === "TR" || aria === "row") {
            emit("row", textDepth, "");
            textDepth += 1;
        } else if (["TD", "TH"].includes(tag) || ["cell", "gridcell", "columnheader", "rowheader"].includes(aria)) {
            const cellRole = aria === "rowheader" || element.scope?.startsWith("row") ? "rowheader"
                : aria === "columnheader" || tag === "TH" ? "columnheader" : "cell";
            emit(cellRole, textDepth, "");
            textDepth += 1;
        } else if (tag === "CAPTION") {
            return;
        } else if ((tag === "A" && element.hasAttribute("href")) || tag === "BUTTON" || aria === "button") {
            const text = label(element) || clean(element.innerText);
            if (text) emit(tag === "A" ? "link" : "button", textDepth, text);
            return;
        } else if (aria === "checkbox" || aria === "radio" || aria === "slider" || aria === "textbox") {
            value = element.getAttribute("aria-valuetext") || element.getAttribute("aria-valuenow") ||
                element.getAttribute("aria-checked") || element.innerText || "";
            emit(aria === "textbox" ? "textfield" : aria, textDepth, label(element), {value});
            return;
        }

        let pending = "";
        const flush = () => {
            const text = clean(pending);
            if (text) emit("text", textDepth, text);
            pending = "";
        };
        for (const child of element.childNodes) {
            if (stop()) { partial = true; break; }
            if (child.nodeType === Node.TEXT_NODE) pending += child.nodeValue;
            else if (child.nodeType === Node.ELEMENT_NODE) {
                if (tag === "DETAILS" && !element.open && child.tagName !== "SUMMARY") continue;
                if (child.tagName === "BR") pending += "\n";
                else if (/^(SPAN|EM|STRONG|B|I|U|CODE|SUP|SUB|ABBR)$/.test(child.tagName) &&
                    !child.hasAttribute("role") && child.childElementCount === 0 && !hidden(child)) {
                    pending += child.textContent;
                } else {
                    flush();
                    walk(child, depth + 1, textDepth);
                }
            }
        }
        flush();
    }
    if (document.body) walk(document.body, 0, 0);
    return JSON.stringify({blocks, partial});
})()
