# IPTV EPG

Guía XMLTV consolidada para Flex IPTV.

## Guía activa en Flex

La guía confirmada en uso por Flex IPTV es:

```text
https://raw.githubusercontent.com/Chevosky/IPTV/main/guide-flex-20261003.xml
```

`guide-flex-20261003.xml` es el nombre estable que usamos en Flex. La generación local la produce junto con `guide-flex.xml`; no se cambia esa ruta.

## Cobertura inicial

- USA
- LATAM: Argentina, Brasil, Chile, Colombia, Costa Rica, República Dominicana, Ecuador, México, Panamá, Perú y Uruguay
- Europa: Reino Unido, España, Francia, Italia, Alemania, Portugal, Países Bajos, Bélgica y Suiza
- FAST: Plex, Samsung TV Plus y Rakuten en varios mercados

La guía base se genera desde fuentes públicas de EPGShare01 y se reduce a una ventana de 48 horas. Luego la guía curada incorpora los mapeos y fuentes exactas usados por la lista de favoritos.

## Compilación local

`publication/scripts/refresh_local_release.py` reemplaza la compilación remota: crea una copia aislada del inventario, captura las fuentes revisadas en tandas locales y produce una generación candidata. No hace commit, push ni publicación.

La salida conserva los contratos de consumo existentes:

- `guide-flex.xml` y `guide-flex-20261003.xml` para Flex.
- `zapper-flex.m3u` para la lista Flex.
- `coordinated-generation.json` para Zapper.

La promoción de esos artefactos a las rutas públicas es una operación separada y explícita; el compilador local no la ejecuta.

## Archivos principales

- `sources.json`: fuentes de la guía base.
- `scripts/build_epg.py`: construye `guide.xml`.
- `publication/scripts/refresh_local_release.py`: orquestador local aislado.
- `publication/scripts/collect_official_expansion.py`: captura local y acotada de proveedores directos.
- `publication/scripts/split_epg_batches.py`: divide la captura IPTV-Org en tandas locales.
