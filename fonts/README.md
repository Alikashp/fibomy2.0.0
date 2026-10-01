# fonts/

`LiberationSans-Regular.ttf`, `LiberationSans-Bold.ttf` — метрически совместимый клон Arial (D-027). По ним fitter (`src/core/fitting`) мерит ширину текста, чтобы переносы совпадали с PowerPoint и с PDF, который LibreOffice набирает тем же шрифтом.

Источник — пакет `fonts-liberation` (Liberation Fonts 2.x). Лицензия — SIL Open Font License 1.1: шрифт можно свободно распространять вместе с программой, в том числе в коммерческом продукте; переименовывать изменённую версию нельзя. Текст лицензии — https://github.com/liberationfonts/liberation-fonts/blob/main/LICENSE.

В PPTX шрифт не встраивается: там стоит Arial.
