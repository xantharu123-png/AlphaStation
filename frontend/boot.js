(function () {
    'use strict';

    function element(tag, text, styles) {
        var node = document.createElement(tag);
        if (text !== undefined && text !== null) node.textContent = String(text);
        Object.assign(node.style, styles || {});
        return node;
    }

    function appendWhenReady(node) {
        if (document.body) {
            if (!document.body.contains(node)) document.body.appendChild(node);
            return;
        }
        document.addEventListener('DOMContentLoaded', function () {
            if (document.body && !document.body.contains(node)) document.body.appendChild(node);
        }, { once: true });
    }

    function showBootError(message) {
        if (document.getElementById('boot-error-overlay')) return;

        var buildMeta = document.querySelector('meta[name="alpha-build"]');
        var buildLabel = buildMeta ? buildMeta.getAttribute('content') : 'unknown';
        var overlay = element('div', null, {
            position: 'fixed',
            inset: '0',
            zIndex: '99999',
            background: '#0f172a',
            color: '#f8fafc',
            padding: '32px',
            fontFamily: 'sans-serif'
        });
        overlay.id = 'boot-error-overlay';

        var frame = element('div', null, {
            maxWidth: '900px',
            margin: '0 auto',
            display: 'flex',
            minHeight: '100vh',
            alignItems: 'center'
        });
        var content = element('div');
        content.appendChild(element('div', 'Alpha Station konnte nicht geladen werden', {
            fontSize: '28px', fontWeight: '800', marginBottom: '12px'
        }));
        content.appendChild(element('div', 'Build ' + String(buildLabel || 'unknown'), {
            fontSize: '12px', letterSpacing: '.08em', textTransform: 'uppercase',
            color: '#94a3b8', marginBottom: '12px'
        }));
        content.appendChild(element(
            'div',
            'Die Seite ist nicht absichtlich leer. Unten steht die konkrete Browser-Fehlermeldung.',
            { fontSize: '15px', lineHeight: '1.7', color: '#cbd5e1', marginBottom: '18px' }
        ));
        content.appendChild(element('pre', message || 'Unbekannter Frontend-Fehler', {
            whiteSpace: 'pre-wrap', wordBreak: 'break-word', background: '#111827',
            border: '1px solid rgba(148,163,184,0.25)', borderRadius: '14px',
            padding: '18px', fontSize: '13px', lineHeight: '1.6', color: '#fda4af'
        }));
        frame.appendChild(content);
        overlay.appendChild(frame);
        appendWhenReady(overlay);
    }

    function resourceFailure(event) {
        var target = event && event.target;
        if (target && target !== window) {
            var location = target.src || target.href || target.tagName || 'unbekannte Ressource';
            return 'Frontend-Ressource konnte nicht geladen werden: ' + String(location);
        }
        return event && (event.message || (event.error && event.error.stack) || event.error);
    }

    window.showBootError = showBootError;
    window.addEventListener('error', function (event) {
        showBootError(resourceFailure(event));
    }, true);
    window.addEventListener('unhandledrejection', function (event) {
        var reason = event ? event.reason : null;
        showBootError((reason && (reason.stack || reason.message)) || reason || 'Unhandled promise rejection');
    });
    window.addEventListener('load', function () {
        window.setTimeout(function () {
            var root = document.getElementById('root');
            if (root && root.children.length === 0) {
                var missing = [];
                if (!window.React) missing.push('React fehlt');
                if (!window.ReactDOM) missing.push('ReactDOM fehlt');
                showBootError(missing.length
                    ? 'Fehlende Runtime: ' + missing.join(', ')
                    : 'Die Runtime ist geladen, aber die App hat nichts in #root gerendert.');
            }
        }, 2500);
    });
}());
