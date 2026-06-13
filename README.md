# Media Expert LEGO Price Tracker

Automatyczny skrypt do sprawdzania cen zestawów LEGO z promocji Media Expert.

## Co robi

1. Pobiera stronę promocji LEGO z kodem.
2. Znajduje aktualny plik `/spark-state/...`.
3. Pobiera dane produktów z JSON-a.
4. Zapisuje aktualne ceny do `data/latest_prices.json`.
5. Dopisuje historię cen do `data/price_history.csv`.
6. GitHub Actions uruchamia skrypt co godzinę.

## Pliki

- `scraper.py` - główny skrypt
- `requirements.txt` - biblioteki Pythona
- `.github/workflows/update_prices.yml` - automatyzacja GitHub Actions
- `data/latest_prices.json` - najnowsze dane
- `data/price_history.csv` - historia cen

## Ręczne uruchomienie lokalnie

```bash
pip install -r requirements.txt
python scraper.py
```

## Ręczne uruchomienie na GitHubie

Wejdź w:

```text
Actions -> Update Media Expert LEGO prices -> Run workflow
```

## Uwaga

Skrypt nie używa Twoich ciasteczek ani danych logowania.
HTML jest używany tylko do znalezienia linku `/spark-state/...`.
Produkty i ceny są pobierane z JSON-a.
