#' The script is designed to download and save public health data for Dengue and Chikungunya from the 
#' Brazilian DATASUS system (using the microdatasus package). It loops through the available years for each disease. 
#' For each year, it fetches the corresponding data from the specified information system (either "SINAN-DENGUE" or 
#' "SINAN-CHIKUNGUNYA-FINAL") and saves it as a compressed CSV file. 
#' The process time is recorded to assess the performance of the script.

#' This script is also designed to automate the retrieval and download of public health surveillance data 
#' related to Severe Acute Respiratory Syndrome (SRAG) from the Brazilian OpenDataSUS platform. 
#' It consists of two main components:
#'
#' 1. The `get_all_srag_links()` function scrapes the OpenDataSUS website for datasets tagged with "SRAG", 
#'    navigates through their resource pages, and extracts the direct download URLs for CSV files containing 
#'    SIVEP-Gripe (INFLUD) microdata. The function returns a unique list of valid download links.
#'
#' 2. The `download_sivep_data()` function takes these links and downloads the corresponding datasets. 
#'    It allows the user to specify which years to download or to retrieve all available years. 
#'    For each file, it applies a retry mechanism with configurable timeout, reads the data using `fread()` 
#'    with `curl`, and saves it as a compressed CSV file (.csv.gz). Any download failures are reported 
#'    at the end of the process.
#'
#' Together, these functions enable efficient and reproducible extraction of SRAG microdata for further analysis. 
#' The script records the total processing time to help evaluate performance.

### Load necessary libraries

if (!requireNamespace("read.dbc", quietly = TRUE)) devtools::install_github("danicat/read.dbc")
if (!requireNamespace("microdatasus", quietly = TRUE)) remotes::install_github("rfsaldanha/microdatasus", force = TRUE)
library(microdatasus)
library(tidyverse)
library(data.table)
library(rvest)
library(stringr)
library(purrr)

# Record the start time of the process
ini = Sys.time()

### DENGUE DATA EXTRACTION AND SAVING

# Loop through the years 2010 to 2025 to download and save data for Dengue
for(year in 2010:2025) {
  
  # Print the current year to track progress
  print(year)
  
  # Fetch data from SINAN-DENGUE system for the specified year
  sinan_data <- fetch_datasus(year_start = year, 
                              year_end = year, 
                              information_system = "SINAN-DENGUE", 
                              timeout = 2000)
  
  # Generate the filename for saving the data
  filename <- paste0("data/epidemiological/SINAN/dengue_",year,".csv.gz")
  
  # Print a message indicating the file being written
  print(paste0("Writing: ", filename))
  
  # Save the data as a compressed CSV file
  fwrite(sinan_data, file = filename, compress="gzip")
  
  # Print a confirmation message once the file is successfully written
  print(paste("Writing: OK"))
  
}
### CHIKUNGUNYA DATA EXTRACTION AND SAVING

# Loop through the years 2015 to 2025 to download and save data for Chikungunya
for(year in 2015:2025) {
  
  # Print the current year to track progress
  print(year)
  
  # Fetch data from SINAN-CHIKUNGUNYA-FINAL system for the specified year
  sinan_data <- fetch_datasus(year_start = year, 
                              year_end = year, 
                              information_system = "SINAN-CHIKUNGUNYA", 
                              timeout = 2000)
  
  # Generate the filename for saving the data
  filename <- paste0("data/epidemiological/SINAN/chikungunya_",year,".csv.gz")
  
  # Print a message indicating the file being written
  print(paste0("Writing: ", filename))
  
  # Save the data as a compressed CSV file
  fwrite(sinan_data, file = filename, compress="gzip")
  
  # Print a confirmation message once the file is successfully written
  print(paste("Writing: OK"))
  
}

### SRAG DATA EXTRACTION AND SAVING

#' Get all SIVEP-SRAG CSV download links from OpenDataSUS
#'
#' This function scrapes the OpenDataSUS SRAG dataset page and recursively extracts
#' all available links to `.csv` files related to SIVEP-SRAG data (files with "INFLUD" in the name).
#'
#' It first finds all datasets tagged with "SRAG", then visits each dataset page,
#' identifies the associated resource pages, and from each resource page, extracts the
#' direct download link for the corresponding CSV file.
#'
#' @return A character vector containing unique URLs of CSV files with SIVEP-SRAG data.
#' 
get_all_srag_links <- function() {
  library(rvest)
  library(stringr)
  library(dplyr)
  library(purrr)
  
  # 1. Main OpenDataSUS page with SRAG tag
  main_page <- "https://opendatasus.saude.gov.br/dataset?tags=SRAG"
  
  # 2. Read the main page HTML
  html_page <- read_html(main_page)
  
  # 3. Extract all dataset links from the main page
  links_dataset <- html_page %>%
    html_elements("a") %>%
    html_attr("href") %>%
    unique() %>%
    str_subset("^/dataset/") %>%
    paste0("https://opendatasus.saude.gov.br", .)
  
  # 4. For each dataset, find resource pages and extract INFLUD*.csv links
  get_sari_links_csv <- function(dataset_url) {
    html_dataset <- tryCatch(read_html(dataset_url), error = function(e) return(NULL))
    if (is.null(html_dataset)) return(NULL)
    
    links_resource <- html_dataset %>%
      html_elements("a") %>%
      html_attr("href") %>%
      unique() %>%
      str_subset("^/dataset/.*/resource/") %>%
      paste0("https://opendatasus.saude.gov.br", .)
    
    links_csv <- map_chr(links_resource, function(resource_url) {
      resource_html <- tryCatch(read_html(resource_url), error = function(e) return(NA))
      if (is.na(resource_html)) return(NA)
      
      all_links <- resource_html %>% html_elements("a") %>% html_attr("href")
      link_influd <- all_links[grepl("^https.*INFLUD.*\\.csv$", all_links)]
      
      if (length(link_influd) == 0) return(NA)
      return(link_influd[1])
    })
    
    links_csv[!is.na(links_csv)]
  }
  
  # 5. Apply to all datasets and return a unique list of CSV links
  all_links <- links_dataset %>%
    map(get_sari_links_csv) %>%
    unlist() %>%
    unique()
  
  return(all_links)
}

#' Download and save SIVEP-SRAG data from OpenDataSUS CSV links
#'
#' This function downloads one or more SIVEP-SRAG `.csv` files from OpenDataSUS, 
#' using curl via `fread()`, with retry logic and customizable timeout.
#'
#' @param all_sari_links_csv Character vector with URLs to the INFLUD CSV files.
#' @param years_to_download Either "all" (default) or a numeric vector of years to download, e.g., c(2023, 2024).
#' @param dest_dir Destination directory where the files will be saved. Defaults to "data/epidemiological/SIVEP".
#' @param max_tries Maximum number of download attempts per file. Default is 3.
#' @param timeout_sec Timeout (in seconds) for each download attempt using curl. Default is 300.
#'
#' @return Downloads the files and saves them as .csv.gz in the specified folder. 
#'         Prints a list of failed downloads, if any.

download_sivep_data <- function(all_sari_links_csv, 
                                years_to_download = "all", 
                                dest_dir = "data/epidemiological/SIVEP", 
                                max_tries = 5, 
                                timeout_sec = 10000) {
  # Load necessary packages
  library(stringr)     # for string manipulation
  library(data.table)  # for fread/fwrite
  library(fs)          # for file system handling (e.g., creating folders)
  
  # Ensure destination folder exists
  dir_create(dest_dir)
  
  # Extract the last two digits of the year from the link using regex
  years_last_digits <- str_extract(all_sari_links_csv, "INFLUD\\d{2}") %>%
    str_extract("\\d{2}")
  
  # Build the full year (e.g., "24" -> "2024")
  years <- paste0("20", years_last_digits)
  
  # If the user requested specific years, filter only those links
  if (!identical(years_to_download, "all")) {
    selected <- years %in% as.character(years_to_download)
    all_sari_links_csv <- all_sari_links_csv[selected]
    years <- years[selected]
  }
  
  # Initialize a vector to store failed downloads
  failed_downloads <- character(0)
  
  # Mark the start time to measure total duration
  ini <- Sys.time()
  
  # Iterate over each CSV URL
  for (i in seq_along(all_sari_links_csv)) {
    sivep_url <- all_sari_links_csv[i]
    year <- years[i]
    filename <- file.path(dest_dir, paste0("INFLUD", year, ".csv.gz"))
    
    message("Reading: ", sivep_url)
    
    success <- FALSE
    attempt <- 1
    
    # Retry loop: attempt to download and read the file
    while (!success && attempt <= max_tries) {
      try({
        # Use curl via fread with timeout and redirect following
        curl_cmd <- paste("curl --max-time", timeout_sec, "-L", shQuote(sivep_url))
        sivep_data <- fread(cmd = curl_cmd)
        success <- TRUE
      }, silent = TRUE)
      
      # If attempt failed, wait a few seconds before retrying
      if (!success) {
        message("Attempt ", attempt, " failed. Retrying in ", 2^attempt, " seconds...")
        Sys.sleep(2 ^ attempt)  # Exponential backoff: 2, 4, 8...
        attempt <- attempt + 1
      }
    }
    
    # If successful, write the data to a compressed CSV file
    if (success) {
      message("Writing: ", filename)
      fwrite(sivep_data, file = filename, compress = "gzip")
    } else {
      # If all attempts failed, record the failure
      message("Failed after ", max_tries, " attempts: ", sivep_url)
      failed_downloads <- c(failed_downloads, paste0("Year ", year, ": ", sivep_url))
    }
  }
  
  # Calculate and print the total execution time
  fim <- Sys.time()
  message("Total time: ", round(difftime(fim, ini, units = "mins"), 2), " minutes")
  
  # Report any failed downloads
  if (length(failed_downloads) > 0) {
    message("\nThe following files failed to download:")
    for (fail in failed_downloads) {
      message("- ", fail)
    }
  } else {
    message("All files downloaded successfully.")
  }
}

# Final result: print all CSV links found
all_sari_links_csv <- get_all_srag_links()

# download all available years
download_sivep_data(all_sari_links_csv, years_to_download = "all")
download_sivep_data(all_sari_links_csv, years_to_download = 2021, timeout_sec = 3000)
# Record the end time of the process
fin = Sys.time()

# Print the total time taken for the entire process
print("Total time:")
print(fin - ini)


