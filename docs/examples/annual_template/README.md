# Resultados de ejemplo

Los JSON/CSV de este directorio fueron producidos por el validador usando libros
temporales vacíos o datos sintéticos. No contienen información comercial privada
y no acreditan identidad ni aprobación de productos reales.

- `empty/`: plantilla vacía; ERROR de captura esperado, sin registros comerciales.
- `synthetic_warning/`: una cadena sintética, un producto, un mes y cero real;
  sin errores, clasificación provisional y homologación informativa MB.
- `synthetic_errors/`: misma muestra con fila histórica duplicada, venta negativa
  y fuente inexistente; ERROR, una fila duplicada, tres errores.

Cada carpeta conserva `validation_summary.json`, `validation_errors.csv` y
`validation_status.json`. Las huellas corresponden a los archivos sintéticos
temporales del momento de generación, no al Excel oficial distribuido.

Para reproducirlos desde la raíz del proyecto:

```sh
python tests/annual_template/create_examples.py
```

El script no modifica la plantilla oficial ni accede a Supabase.
