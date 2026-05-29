# Load necessary libraries
# tidyverse: For data manipulation and handling
# lubridate: For working with dates
# data.table: To efficiently read and write large datasets
require(tidyverse)
require(lubridate)
require(data.table)
require(R.utils)


# Load source custom functions from an external R script
source("r/functions/fun.R")

###################################################################################
### Dengue Data Processing
###################################################################################

# List all files related to Dengue data in the specified directory
file_list <- list.files(path = "data/epidemiological/SINAN",
                        pattern = "^dengue_\\d{4}\\.csv\\.gz$")

# Record the start time of the process
ini = Sys.time()

# Initialize an empty data.table to store the summarized Dengue data
dengue_data_summary = data.table()

# Loop through each Dengue data file in the file list
for(f in file_list) {
  
  # Construct the full path to the file
  read_filename = paste0("data/epidemiological/SINAN/",f)
  
  # Print a message indicating the file being read
  print(paste0("Reading: ", read_filename))
  
  # Extract the year from the file name to determine the appropriate processing steps
  file_year <- as.numeric(substr(f, 8,11))
  
  # Read the file and perform data processing
  # Different sets of columns are selected depending on the year
  if(file_year <= 2013) {
    
    # Select basic columns for years up to 2013
    dengue_data_summary_year <- fread(read_filename,
                                      select = c("DT_DIGITA", "DT_NOTIFIC", "DT_SIN_PRI", "SG_UF_NOT", "CLASSI_FIN"))
    
  } else {
    
    # Select additional symptom-related columns for years after 2013
    dengue_data_summary_year <- fread(read_filename,
                                      select = c("DT_DIGITA", "DT_NOTIFIC", "DT_SIN_PRI", "SG_UF_NOT", "CLASSI_FIN",
                                                 "FEBRE", "MIALGIA", "CEFALEIA", "EXANTEMA", "VOMITO", "NAUSEA", 
                                                 "DOR_COSTAS", "CONJUNTVIT", "ARTRITE", "ARTRALGIA", "PETEQUIA_N", 
                                                 "LEUCOPENIA", "LACO", "DOR_RETRO"))
  }
  
  if(!"DT_DIGITA" %in% colnames(dengue_data_summary_year)) dengue_data_summary_year$DT_DIGITA <- NA
  
  # Convert notification and symptom onset dates to Date objects and filter out invalid date ranges
  dengue_data_summary_year <- dengue_data_summary_year %>%
    
    # Convert notification and symptom onset dates to Date objects
    mutate(DT_DIGITA = as.Date(DT_DIGITA),
           DT_SIN_PRI = as.Date(DT_SIN_PRI),
           DT_NOTIFIC = as.Date(DT_NOTIFIC)) %>%
    
    # Convert dates to the first day of the epidemiological week
    mutate(SE_DIG = floor_date(DT_DIGITA, unit = "week"),
           SE_SIN_PRI = floor_date(DT_SIN_PRI, unit = "week"),
           SE_NOT = floor_date(DT_NOTIFIC, unit = "week")) %>%
    
    # Calculate the difference between symptom onset date and notification date
    mutate(dif_datas = as.numeric(DT_SIN_PRI - DT_NOTIFIC)) %>%
    
    # Filter out records where the difference is outside the range of -90 to 180 days
    filter(dif_datas > -180 & dif_datas < 1) %>%
    
    # Remove the difference column as it's no longer needed
    select(-dif_datas) %>%
    
    # Remove the original date columns
    select(-DT_NOTIFIC, -DT_SIN_PRI, -DT_DIGITA)
  
  # Process the data differently based on the year
  if(file_year <= 2013) {
    
    # For years up to 2013, group by epidemiological weeks, classification, and state, and count occurrences
    dengue_data_summary_year <- dengue_data_summary_year %>%
      count(SE_DIG, SE_NOT, SE_SIN_PRI, CLASSI_FIN, SG_UF_NOT) 
    
  } else { 
    
    # For years after 2013, transform symptom data (values not equal to 1 are set to NA) and summarize the data
    dengue_data_summary_year <- dengue_data_summary_year %>%
      mutate(across(
        .cols = -all_of(c("SE_DIG", "SE_NOT", "SE_SIN_PRI", "CLASSI_FIN", "SG_UF_NOT")), # Select all columns except those that are not symptoms
        .fns = ~ ifelse(. != 1, NA, .) )) %>% # Apply the rule: if value is not 1 ('Yes'), change to NA
      group_by(SE_DIG, SE_NOT, SE_SIN_PRI, CLASSI_FIN, SG_UF_NOT) %>%
      summarise(n = n(),
                FEBRE = sum(FEBRE, na.rm = TRUE),
                MIALGIA = sum(MIALGIA, na.rm = TRUE),
                CEFALEIA = sum(CEFALEIA, na.rm = TRUE),
                EXANTEMA = sum(EXANTEMA, na.rm = TRUE),
                VOMITO = sum(VOMITO, na.rm = TRUE),
                NAUSEA = sum(NAUSEA, na.rm = TRUE),
                DOR_COSTAS = sum(DOR_COSTAS, na.rm = TRUE),
                CONJUNTVIT = sum(CONJUNTVIT, na.rm = TRUE),
                ARTRITE = sum(ARTRITE, na.rm = TRUE),
                ARTRALGIA = sum(ARTRALGIA, na.rm = TRUE),
                PETEQUIA_N = sum(PETEQUIA_N, na.rm = TRUE),
                LEUCOPENIA = sum(LEUCOPENIA, na.rm = TRUE),
                LACO = sum(LACO, na.rm = TRUE),
                DOR_RETRO = sum(DOR_RETRO, na.rm = TRUE)) %>%
      ungroup()
  }
  
  # Arrange the summarized data by state, notification week, symptom onset week, and classification
  dengue_data_summary_year <- dengue_data_summary_year %>%
    arrange(SG_UF_NOT, SE_DIG, SE_NOT, SE_SIN_PRI, CLASSI_FIN)
  
  # Append the summarized data for the current year to the overall summary
  dengue_data_summary = bind_rows(dengue_data_summary, dengue_data_summary_year)
  
  # Remove the temporary data table for the current year and trigger garbage collection
  rm(dengue_data_summary_year);gc()
}

# Record the end time of the process
fin = Sys.time()

colnames(dengue_data_summary) <- c("ew_recorded", "ew_notification", "ew_symptom_onset", "final_classification", "state_abbrev", "case_count", "fever", "myalgia", 
"headache", "rash", "vomiting", "nausea", "back_pain", "conjunctivitis", "arthritis", "arthralgia", "petechiae", "leukopenia", 
"tourniquet_test", "retro-orbital_pain")

year_range_dengue <- substr(file_list, 8,11)

# Save the summarized Dengue data to a compressed CSV file
filename = paste0("data/epidemiological/SINAN/SINAN_dengue_cases.csv.gz")
fwrite(dengue_data_summary, file = filename, compress = "gzip")

###################################################################################
### Chikungunya Data Processing
###################################################################################

# List all files related to Chikungunya data in the specified directory
file_list <- list.files(path = "data/epidemiological/SINAN",
                        pattern = "^chikungunya_\\d{4}\\.csv\\.gz$")

# Record the start time of the process
ini = Sys.time()

# Initialize an empty data.table to store the summarized Chikungunya data
chik_data_summary = data.table()

# Loop through each Chikungunya data file in the file list
for(f in file_list) {
  
  # Construct the full path to the file
  read_filename = paste0("data/epidemiological/SINAN/",f)
  
  # Print a message indicating the file being read
  print(paste0("Reading: ", read_filename))
  
  # Extract the year from the file name to determine the appropriate processing steps
  file_year <- as.numeric(substr(f, 13,16))
  
  # Read the file and perform data processing
  # Different sets of columns are selected depending on the year
  if(file_year <= 2016) {
    
    # For years up to 2016, select only the basic columns
    columns = c("DT_NOTIFIC", "DT_SIN_PRI", "SG_UF_NOT", "CLASSI_FIN")
    
    # Define the data types for each column
    # Dates are stored as Date objects; other columns as character strings
    column_types <- c("Date", "Date", rep("character", 2))
    names(column_types) = columns
    
  }
  
  if(file_year > 2016 & file_year <= 2020) {  
    # For years after 2016, select additional columns related to symptoms
    columns = c("DT_NOTIFIC", "DT_SIN_PRI", "SG_UF_NOT", "CLASSI_FIN",
                "FEBRE", "MIALGIA", "CEFALEIA", "EXANTEMA", "VOMITO", "NAUSEA", 
                "DOR_COSTAS", "CONJUNTVIT", "ARTRITE", "ARTRALGIA", "PETEQUIA_N", 
                "LEUCOPENIA", "LACO", "DOR_RETRO")
    
    # Define the data types for each column
    # Dates are stored as Date objects; all symptom-related and classification columns as character strings
    column_types <- c("Date", "Date", rep("character", 16))
    names(column_types) = columns
  }
  
  if(file_year >2020) {  
    # For years after 2016, select additional columns related to symptoms
    columns = c("DT_DIGITA", "DT_NOTIFIC", "DT_SIN_PRI", "SG_UF_NOT", "CLASSI_FIN",
                "FEBRE", "MIALGIA", "CEFALEIA", "EXANTEMA", "VOMITO", "NAUSEA", 
                "DOR_COSTAS", "CONJUNTVIT", "ARTRITE", "ARTRALGIA", "PETEQUIA_N", 
                "LEUCOPENIA", "LACO", "DOR_RETRO")
    
    # Define the data types for each column
    # Dates are stored as Date objects; all symptom-related and classification columns as character strings
    column_types <- c("Date", "Date", "Date", rep("character", 16))
    names(column_types) = columns
  }
  
  # Read the data file using 'fread' from the 'data.table' package
  # Specify the selected columns and their data types
  # Ensure proper character encoding with 'UTF-8' to handle special characters
  chik_data_summary_year <- fread(read_filename,
                                  select = columns,
                                  colClasses = column_types,
                                  encoding = 'UTF-8')
  

  
  if(!"DT_DIGITA" %in% colnames(chik_data_summary_year)) chik_data_summary_year$DT_DIGITA <- NA
  
  # Convert notification and symptom onset dates to Date objects and filter out invalid date ranges
  chik_data_summary_year <- chik_data_summary_year %>%
    
    # Calculate the difference between symptom onset date and notification date
    mutate(dif_datas = as.numeric(DT_SIN_PRI - DT_NOTIFIC)) %>%
    
    # Filter out records where the difference is outside the range of -90 to 180 days
    filter(dif_datas > -180 & dif_datas < 1) %>%
    
    # Remove the difference column as it's no longer needed
    select(-dif_datas) %>%
    
    # Convert notification and symptom onset dates to Date objects
    mutate(DT_DIGITA = as.Date(DT_DIGITA),
           DT_SIN_PRI = as.Date(DT_SIN_PRI),
           DT_NOTIFIC = as.Date(DT_NOTIFIC)) %>%
    
    # Convert dates to the first day of the epidemiological week
    mutate(SE_DIG = floor_date(DT_DIGITA, unit = "week"),
           SE_SIN_PRI = floor_date(DT_SIN_PRI, unit = "week"),
           SE_NOT = floor_date(DT_NOTIFIC, unit = "week")) %>%
    
    # Remove the original date columns
    select(-DT_NOTIFIC, -DT_SIN_PRI, -DT_DIGITA) %>%
    
    # Convert the 'CLASSI_FIN' column from Latin-1 encoding to UTF-8 to ensure proper character representation
    mutate(CLASSI_FIN = iconv(CLASSI_FIN, from = "latin1", to = "UTF-8")) %>%
    
    # Convert the 'CLASSI_FIN' column to a numeric data type, likely to facilitate numerical operations or comparisons
    mutate(CLASSI_FIN = as.numeric(CLASSI_FIN)) 
  
  # Process the data differently based on the year
  if(file_year <= 2016) {
    
    # For years up to 2016, group by epidemiological weeks, classification, and state, and count occurrences
    chik_data_summary_year <- chik_data_summary_year %>%
      count(SE_DIG, SE_NOT, SE_SIN_PRI, CLASSI_FIN, SG_UF_NOT) 
    
  } else { 
    
    # For years after 2016, transform symptom data (values not equal to 1 are set to NA) and summarize the data
    chik_data_summary_year <- chik_data_summary_year %>%
      mutate(across(
        .cols = -all_of(c("SE_DIG", "SE_NOT", "SE_SIN_PRI", "CLASSI_FIN", "SG_UF_NOT")), # Select all columns except those that are not symptoms
        .fns = ~ as.numeric(ifelse(. != 1, NA, .) ))) %>% # Apply the rule: if value is not 1 ('Yes'), change to NA
      group_by(SE_DIG, SE_NOT, SE_SIN_PRI, CLASSI_FIN, SG_UF_NOT) %>%
      summarise(n = n(),
                FEBRE = sum(FEBRE, na.rm = TRUE),
                MIALGIA = sum(MIALGIA, na.rm = TRUE),
                CEFALEIA = sum(CEFALEIA, na.rm = TRUE),
                EXANTEMA = sum(EXANTEMA, na.rm = TRUE),
                VOMITO = sum(VOMITO, na.rm = TRUE),
                NAUSEA = sum(NAUSEA, na.rm = TRUE),
                DOR_COSTAS = sum(DOR_COSTAS, na.rm = TRUE),
                CONJUNTVIT = sum(CONJUNTVIT, na.rm = TRUE),
                ARTRITE = sum(ARTRITE, na.rm = TRUE),
                ARTRALGIA = sum(ARTRALGIA, na.rm = TRUE),
                PETEQUIA_N = sum(PETEQUIA_N, na.rm = TRUE),
                LEUCOPENIA = sum(LEUCOPENIA, na.rm = TRUE),
                LACO = sum(LACO, na.rm = TRUE),
                DOR_RETRO = sum(DOR_RETRO, na.rm = TRUE)) %>%
      ungroup()
  }
  
  # Arrange the summarized data by state, notification week, symptom onset week, and classification
  chik_data_summary_year <- chik_data_summary_year %>%
    arrange(SG_UF_NOT, SE_DIG, SE_NOT, SE_SIN_PRI, CLASSI_FIN)
  
  # Append the summarized data for the current year to the overall summary
  chik_data_summary = bind_rows(chik_data_summary, chik_data_summary_year)
  
  if(sum(is.na(chik_data_summary$SE_SIN_PRI)) > 0) {stop()}
  # Remove the temporary data table for the current year and trigger garbage collection
  rm(chik_data_summary_year);gc()
}

# Record the end time of the process
fin = Sys.time()

colnames(chik_data_summary) <- c("ew_recorded", "ew_notification", "ew_symptom_onset", "final_classification", 
                                 "state_abbrev", "case_count", "fever", "myalgia", "headache", "rash", "vomiting", 
                                 "nausea", "back_pain", "conjunctivitis", "arthritis", "arthralgia", "petechiae", 
                                 "leukopenia", "tourniquet_test", "retro-orbital_pain")

# Save the summarized Chikungunya data to a compressed CSV file

year_range_chik <- substr(file_list, 13,16)

# Save the summarized Dengue data to a compressed CSV file
filename = paste0("data/epidemiological/SINAN/SINAN_chik_cases.csv.gz")
fwrite(chik_data_summary, file = filename, compress="gzip")



