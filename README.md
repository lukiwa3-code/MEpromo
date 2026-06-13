# Media Expert LEGO Price Tracker

Automatyczny skrypt do sprawdzania cen zestawów LEGO z promocji Media Expert.

## Ważna poprawka

GitHub Actions może dostawać `403 Forbidden` przy próbie wejścia na stronę listingu Media Expert. Dlatego skrypt startuje bezpośrednio od `spark-state`, który działał w trybie incognito.

Domyślny adres jest w `scraper.py`:

```python
DEFAULT_SPARK_STATE_URL = "https://www.mediaexpert.pl/spark-state/30272cb24e-96a041-f2b27e-7efc1b"
```

Możesz go podmienić przez zmienną środowiskową `SPARK_STATE_URL` albo podać kilka adresów po przecinku w `SPARK_STATE_URLS`.
