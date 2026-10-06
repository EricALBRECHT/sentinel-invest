# Bibliothèques servies localement

Intégration : 6 octobre 2026.
Ces fichiers sont servis par Sentinel sous `/static/vendor/`. Les pages HTML ne chargent plus de script depuis un CDN.

## HTMX 2.0.4

- Fichier : `htmx.min.js`
- Source officielle : https://github.com/bigskysoftware/htmx (`htmx.org@2.0.4`, `dist/htmx.min.js`)
- Licence : Zero-Clause BSD (0BSD)

## Chart.js 4.4.6

- Fichier : `chart.umd.min.js`
- Source officielle : https://www.chartjs.org et https://github.com/chartjs/Chart.js (`chart.js@4.4.6`, `dist/chart.umd.js`)
- Licence : MIT
- Le build UMD officiel est déjà minifié. Il est publié ici sous le nom `chart.umd.min.js`.

## Mermaid 11.17.2

- Fichier : `mermaid.min.js`
- Source officielle : https://github.com/mermaid-js/mermaid (`mermaid@11.17.2`, `dist/mermaid.min.js`)
- Licence : MIT
- Version figée du canal `mermaid@11` précédemment chargé depuis un CDN.

## Exception Content-Security-Policy

Mermaid insère un élément `<style>` pendant le dessin des diagrammes. Les pages HTML Sentinel autorisent donc `style-src 'self' 'unsafe-inline'`.

`script-src` reste `'self'`. Aucun script inline n’est nécessaire. HTMX est démarré avec `allowEval` désactivé : les pages n’utilisent pas `hx-on` ni d’expressions `javascript:`.
