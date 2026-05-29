# Load necessary libraries
# tidyverse: For data manipulation and handling
# lubridate: For working with dates
# data.table: To efficiently read and write large datasets
require(tidyverse)
require(lubridate)
require(data.table)

# Load source custom functions from an external R script
source("r/functions/fun.R")

###################################################################################
### SIVEP Data Processing
###################################################################################

# List all files related to Dengue data in the specified directory
file_list <- list.files(path = "data/epidemiological/SIVEP", pattern = "INFLUD")

# Record the start time of the process
ini = Sys.time()

# Initialize an empty data.table to store the summarized Dengue data
sivep_data_summary = data.table()

na_date_tbl <- data.frame()

# Loop through each SIVEP data file in the file list
for(f in file_list) {
  
  # Construct the full path to the file
  read_filename = paste0("data/epidemiological/SIVEP/",f)
  
  # Print a message indicating the file being read
  print(paste0("Reading: ", read_filename))
  
  # Extract the year from the file name to determine the appropriate processing steps
  file_year <- as.numeric(substr(f, 9,11)) + 2000
  
  # Different sets of columns are selected depending on the year
  selected_cols = c("DT_DIGITA", "DT_NOTIFIC", "DT_SIN_PRI", "SG_UF_NOT", "CLASSI_FIN", "FEBRE", "TOSSE",
                    "GARGANTA", "DISPNEIA", "DESC_RESP", "DIARREIA")
  
  if(file_year == 2019) {
    selected_cols = c("DT_DIGITA", "DT_NOTIFIC", "DT_SIN_PRI", "SG_UF_NOT", "CLASSI_FIN", "FEBRE", "TOSSE",
                      "GARGANTA", "DISPNEIA", "DESC_RESP", "DIARREIA", "VOMITO",
                      "POS_PCRFLU") 
  }
  
  if(file_year > 2019) {
    selected_cols = c("DT_DIGITA", "DT_NOTIFIC", "DT_SIN_PRI", "SG_UF_NOT", "CLASSI_FIN", "FEBRE", "TOSSE",
                      "GARGANTA", "DISPNEIA", "DESC_RESP", "DIARREIA", "VOMITO", "PERD_OLFT",
                      "PERD_PALA", "DOR_ABD", "FADIGA",
                      "PCR_SARS2","AN_SARS2", "POS_PCRFLU", "POS_AN_FLU") 
  }
  
  # Read the file and perform data processing
  sivep_data_summary_year <- fread(read_filename, select = selected_cols,
                                   colClasses = c("SG_UF_NOT" = "character"))
    
  # Convert notification and symptom onset dates to Date objects and filter out invalid date ranges
  sivep_data_summary_year <- sivep_data_summary_year %>%
    
    # Convert notification and symptom onset dates to Date objects
    mutate(DT_DIGITA = as.Date(DT_DIGITA, format = "%d/%m/%Y"),
           DT_SIN_PRI = as.Date(DT_SIN_PRI, format = "%d/%m/%Y"),
           DT_NOTIFIC = as.Date(DT_NOTIFIC, format = "%d/%m/%Y")) %>%
    
    # Convert dates to the first day of the epidemiological week
    mutate(SE_DIG = floor_date(DT_DIGITA, unit = "week"),
           SE_SIN_PRI = floor_date(DT_SIN_PRI, unit = "week"),
           SE_NOT = floor_date(DT_NOTIFIC, unit = "week")) %>%
    
    # # Calculate the difference between symptom onset date and notification date
    # mutate(dif_datas = as.numeric(DT_SIN_PRI - DT_NOTIFIC)) %>%
    # 
    # # Filter out records where the difference is outside the range of -180 to 180 days
    # filter(dif_datas > -180 & dif_datas < 180) %>%
    # 
    # # Remove the difference column as it's no longer needed
    # select(-dif_datas) %>%

    select(-DT_DIGITA, -DT_NOTIFIC, -DT_SIN_PRI) %>%
    
    mutate(CLASSI_FIN = ifelse(CLASSI_FIN == 3, 2, CLASSI_FIN)) %>%
    
    mutate(CLASSI_FIN = ifelse(CLASSI_FIN == 9, NA, CLASSI_FIN)) %>%
    
    mutate(CLASSI_FIN2 = CLASSI_FIN)
    
  if(file_year == 2019) {
    sivep_data_summary_year <- sivep_data_summary_year %>%
      mutate(CLASSI_FIN = ifelse(POS_PCRFLU == 1, 1, CLASSI_FIN)) %>%
      select(-POS_PCRFLU)
  }
  
  if(file_year > 2019) {
    sivep_data_summary_year <- sivep_data_summary_year %>%
      
    mutate(is_covid = (CLASSI_FIN == 5 | PCR_SARS2 == 1 | AN_SARS2 == 1),
           is_flu = (CLASSI_FIN == 1 | POS_PCRFLU == 1 | POS_AN_FLU == 1)) %>%
      replace_na(list(is_covid = FALSE, is_flu = FALSE)) %>%

      
      mutate(CLASSI_FIN = case_when(is_covid & !is_flu ~ 5,
                                    !is_covid & is_flu  ~ 1,
                                    is_covid & is_flu ~ 0,
                                    TRUE ~ CLASSI_FIN)) %>%
      
      select(-is_covid, -is_flu, -PCR_SARS2, -AN_SARS2, -POS_PCRFLU, -POS_AN_FLU)
  }
    
    # Transform symptom data (values not equal to 1 are set to NA) and summarize the data
    sivep_data_summary_year <- sivep_data_summary_year %>%
      mutate(across(
        .cols = -all_of(c("SE_DIG", "SE_NOT", "SE_SIN_PRI", "CLASSI_FIN", "SG_UF_NOT", "CLASSI_FIN2")), # Select all columns except those that are not symptoms
        .fns = ~ ifelse(. != 1, NA, .) )) %>% # Apply the rule: if value is not 1 ('Yes'), change to NA
      group_by(SE_DIG, SE_NOT, SE_SIN_PRI, CLASSI_FIN, SG_UF_NOT, CLASSI_FIN2) %>%
      summarise(n = n(),
                across(
                  .cols = -all_of("n"), # Select all columns except those that are not symptoms
                  .fns = ~ sum(., na.rm = TRUE))
                ) %>%
      ungroup()
  
  # Arrange the summarized data by state, notification week, symptom onset week, and classification
  sivep_data_summary_year <- sivep_data_summary_year %>%
    arrange(SG_UF_NOT, SE_DIG, SE_NOT, SE_SIN_PRI, CLASSI_FIN, CLASSI_FIN2)
  
  # Append the summarized data for the current year to the overall summary
  sivep_data_summary = bind_rows(sivep_data_summary, sivep_data_summary_year)
  
  na_date_row <- c(file_year, 
    sum(is.na(sivep_data_summary_year$SE_DIG)),
    sum(is.na(sivep_data_summary_year$SE_NOT)),
    sum(is.na(sivep_data_summary_year$SE_SIN_PRI)))
  
  na_date_tbl <- rbind(na_date_tbl, na_date_row)
  
  # Remove the temporary data table for the current year and trigger garbage collection
  rm(sivep_data_summary_year);gc()
}

# Record the end time of the process
fin = Sys.time()

UFs <- read.csv("data/epidemiological/br_federative_units.csv")

sivep_data_summary2 <- sivep_data_summary %>%
  left_join(UFs %>% select(CODE, ABBREVIATION ) %>% mutate(CODE = as.character(CODE)), by = c("SG_UF_NOT" = "CODE")) %>%
  mutate(is_number = as.numeric(SG_UF_NOT)) %>%
  mutate(SG_UF_NOT = ifelse(is.na(is_number), SG_UF_NOT, ABBREVIATION )) %>%
  select(-is_number, - ABBREVIATION )

colnames(sivep_data_summary2) <- c("ew_recorded", "ew_notification", "ew_symptom_onset", "final_classification_original", "state_abbrev", 
  "final_classification_new", "case_count", "fever", "cough", "sore_throat", "breath_shortness", "resp_distress", 
  "diarrhea", "vomiting", "smell_loss", "taste_loss", "ab_pain", "fatigue")

# Save the summarized SIVEP data to a compressed CSV file
filename = "data/epidemiological/SIVEP/SIVEP_cases.csv.gz"
fwrite(sivep_data_summary2, file = filename, compress = "gzip")

