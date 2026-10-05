# Sicurezza

## Versioni supportate

OllaDesk è un progetto personale: le correzioni di sicurezza escono solo
nell'ultima release pubblicata. Chi usa una versione precedente deve
aggiornare.

| Versione                 | Correzioni di sicurezza |
| ------------------------ | ----------------------- |
| ultima release (0.3.x)   | ✅                      |
| versioni precedenti      | ❌ (aggiornare)         |

## Come segnalare una vulnerabilità

Non aprire una issue pubblica. Usa la segnalazione privata di GitHub:
scheda **Security** del repository → **Report a vulnerability**
(<https://github.com/gradia64/OllaDesk/security/advisories/new>).

Indica la versione di OllaDesk, il sistema operativo, i passi per
riprodurre il problema e l'impatto che ti aspetti. Di norma rispondo entro
una settimana; la correzione esce in una nuova release, e la segnalazione
viene resa pubblica (con il tuo nome, se lo desideri) dopo il rilascio.

## Perimetro

Rientrano nel perimetro soprattutto:

- la **companion web** (`olladesk/companion.py`, `olladesk/web/`): accesso
  senza abbinamento, aggiramento del codice di 6 cifre o del limite ai
  tentativi, furto del token di sessione con mezzi diversi dall'ascolto
  della rete (vedi sotto), esposizione di dati che la pagina non deve
  mostrare (percorsi dei file, allegati, risultati web completi);
- la gestione dei file locali: conversazioni, impostazioni, token dei
  dispositivi e chiave API della ricerca web;
- il processo di rilascio: firme dei pacchetti e verifica degli
  aggiornamenti.

Non rientrano:

- l'API di Ollama, che non ha autenticazione per scelta del progetto
  Ollama: esporla in rete (`OLLAMA_HOST=0.0.0.0`) è una scelta
  dell'utente, documentata nel README;
- i **limiti noti della companion web**, già dichiarati nel README e nel
  dialogo di abbinamento: il collegamento è **HTTP in chiaro**, quindi chi
  è sulla stessa rete può intercettare il cookie di sessione. È pensata per
  una rete domestica protetta (WPA2/WPA3), con la porta mai aperta sul
  router. Un HTTPS con certificato autofirmato è previsto in una versione
  successiva.
