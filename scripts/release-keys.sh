# Ruoli delle chiavi di rilascio, letti da scripts/verify-tag.sh,
# scripts/sign-release.sh e scripts/prepare-aur.sh. Unico punto in cui sono
# scritti: vedi il modello di fiducia in packaging/README.md.
#
# Una primaria, sempre offline sulla macchina del maintainer: è l'impronta di
# validpgpkeys nel PKGBUILD e quella pubblicata nel README. Resta [SC] e non
# solo [C] perché ha firmato gli allegati fino alla 0.2.4: togliendole la
# capacità di firma quelle firme non sarebbero più verificabili. Non firma
# più nulla.
RELEASE_PRIMARY="${OLLADESK_RELEASE_PRIMARY:-5B166C1B4AD7428C74A07D5BA3370987A0576694}"
# Sottochiavi che possono firmare i TAG: solo quelle del maintainer, mai
# presenti nella CI. Più di una, separate da spazi, solo durante una
# rotazione.
TAG_SIGNING_SUBKEYS="${OLLADESK_TAG_SIGNING_SUBKEYS:-F8C633AF7119D92C8DB41B2AEE3A018E7436BA92}"
# Sottochiave che firma gli ALLEGATI della release, l'unica nei secret
# (environment «release» di GitHub). Se la CI viene compromessa si revoca
# solo questa: i tag passati restano verificabili.
CI_SIGNING_SUBKEY="${OLLADESK_CI_SIGNING_SUBKEY:-55622405B28224BFABA5AE37C7B95FD2051551BB}"
