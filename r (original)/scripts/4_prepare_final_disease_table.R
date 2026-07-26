# Load required packages
require(tidyverse)
require(data.table)

########## Load federative units data
FUs <- read.csv("data/epidemiological/br_federative_units.csv", colClasses = "factor")

########## Load and process arbovirus data
dn_ts <- fread("data/epidemiological/SINAN/SINAN_dengue_cases.csv.gz", 
               select = c("ew_symptom_onset", "final_classification", "state_abbrev", "case_count"),
               colClasses = c("ew_symptom_onset" = "Date", "final_classification" = "numeric", 
                              "state_abbrev" = "factor", "case_count" = "numeric"))

ck_ts <- fread("data/epidemiological/SINAN/SINAN_chik_cases.csv.gz", 
               select = c("ew_symptom_onset", "final_classification", "state_abbrev", "case_count"),
               colClasses = c("ew_symptom_onset" = "Date", "final_classification" = "numeric", 
                              "state_abbrev" = "factor", "case_count" = "numeric"))

# Process dengue and chikungunya
dn_ts <- dn_ts %>%
  filter(final_classification != 5 | is.na(final_classification)) %>%
  complete(ew_symptom_onset, state_abbrev) %>%
  group_by(ew_symptom_onset, state_abbrev, .add = TRUE) %>%
  summarise(case_count = sum(case_count, na.rm = TRUE)) %>%
  ungroup() %>%
  rename(dengue_cases = case_count) %>%
  drop_na(ew_symptom_onset)

ck_ts <- ck_ts %>%
  filter(final_classification != 5 | is.na(final_classification)) %>%
  complete(ew_symptom_onset, state_abbrev) %>%
  group_by(ew_symptom_onset, state_abbrev, .add = TRUE) %>%
  summarise(case_count = sum(case_count, na.rm = TRUE)) %>%
  ungroup() %>%
  rename(chik_cases = case_count)

arbo_ts <- dn_ts %>%
  full_join(ck_ts, by = c("ew_symptom_onset", "state_abbrev")) %>%
  left_join(FUs %>% select(CODE, ABBREVIATION), by = c("state_abbrev" = "CODE")) %>%
  select(-state_abbrev) %>%
  rename(location = ABBREVIATION)

########## Load and process SARI (SIVEP-Gripe) data
sari_ts <- fread("data/epidemiological/SIVEP/SIVEP_cases.csv.gz",
                 select = c("ew_symptom_onset", "final_classification_new", "state_abbrev", "case_count"),
                 colClasses = c("ew_symptom_onset" = "Date", "final_classification_new" = "numeric", 
                                "state_abbrev" = "factor", "case_count" = "numeric")) %>%
  mutate(ew_symptom_onset = as.Date(ew_symptom_onset))

# Build disaggregated SARI datasets
all_sari_ts <- sari_ts %>%
  complete(ew_symptom_onset, state_abbrev) %>%
  group_by(ew_symptom_onset, state_abbrev, .add = TRUE) %>%
  summarise(case_count = sum(case_count, na.rm = TRUE)) %>%
  ungroup() %>%
  rename(sari_cases = case_count, location = state_abbrev)

covid_ts <- sari_ts %>%
  filter(final_classification_new %in% c(5, 0)) %>%
  complete(ew_symptom_onset, state_abbrev) %>%
  group_by(ew_symptom_onset, state_abbrev, .add = TRUE) %>%
  summarise(case_count = sum(case_count, na.rm = TRUE)) %>%
  ungroup() %>%
  rename(covid_cases = case_count, location = state_abbrev)

influenza_ts <- sari_ts %>%
  filter(final_classification_new %in% c(1, 0)) %>%
  complete(ew_symptom_onset, state_abbrev) %>%
  group_by(ew_symptom_onset, state_abbrev, .add = TRUE) %>%
  summarise(case_count = sum(case_count, na.rm = TRUE)) %>%
  ungroup() %>%
  rename(flu_cases = case_count, location = state_abbrev)

other_ts <- sari_ts %>%
  filter(final_classification_new %in% c(2, 4)) %>%
  complete(ew_symptom_onset, state_abbrev) %>%
  group_by(ew_symptom_onset, state_abbrev, .add = TRUE) %>%
  summarise(case_count = sum(case_count, na.rm = TRUE)) %>%
  ungroup() %>%
  rename(other_sari_cases = case_count, location = state_abbrev)

ignored_sari_ts <- sari_ts %>%
  filter(is.na(final_classification_new)) %>%
  complete(ew_symptom_onset, state_abbrev) %>%
  group_by(ew_symptom_onset, state_abbrev, .add = TRUE) %>%
  summarise(case_count = sum(case_count, na.rm = TRUE)) %>%
  ungroup() %>%
  rename(na_sari_cases = case_count, location = state_abbrev)

# Combine all SARI subtypes
all_sari_ts <- all_sari_ts %>%
  left_join(covid_ts, by = c("ew_symptom_onset", "location")) %>%
  left_join(influenza_ts, by = c("ew_symptom_onset", "location")) %>%
  left_join(other_ts, by = c("ew_symptom_onset", "location")) %>%
  left_join(ignored_sari_ts, by = c("ew_symptom_onset", "location"))

########## Merge all disease data
dis_ts <- arbo_ts %>%
  mutate(ew_symptom_onset = as.Date(ew_symptom_onset)) %>%
  replace_na(list(chik_cases = 0)) %>%
  mutate(arbo_cases = dengue_cases + chik_cases) %>%
  full_join(all_sari_ts, by = c("ew_symptom_onset", "location"))

# Create Brazil-wide aggregation
dis_br_ts <- dis_ts %>%
  group_by(ew_symptom_onset) %>%
  summarise(across(.cols = contains("case"), .fns = ~ sum(., na.rm = TRUE))) %>%
  ungroup()

# Add Brazil-wide row to the dataset
dis_ts <- dis_ts %>%
  bind_rows(dis_br_ts) %>%
  mutate(location = as.character(location)) %>%
  mutate(location = ifelse(is.na(location), "BR", location)) %>%
  relocate(ew_symptom_onset, location)

# Rename final column names
colnames(dis_ts) <- c("ew_symptom_onset", "location", "dengue_cases", "chik_cases", "arbo_cases",
                      "sari_cases", "covid_cases", "flu_cases", "other_sari_cases", "na_sari_cases")

# Save output file
fwrite(dis_ts, file = "data/epidemiological/Arbo_SARI_disease_table.csv")

