# Load required packages
# Install 'gtrendsAPI' from GitHub if not already installed
if (!requireNamespace("gtrendsAPI", quietly = TRUE)) 
  devtools::install_github("racorreia/gtrendsAPI", build_vignettes = TRUE)

# Load packages into the session
require(gtrendsAPI)
require(tidyverse)
require(data.table)

# Load custom functions from local scripts
source("r/functions/fun.R")
source("r/functions/getGraph2.R")

########################################################################
# IMPORTANT
# Define here your api_key

your_api_key <- "..."

########################################################################

# Load the list of Brazilian federative units (states)
FUs <- read.csv("data/epidemiological/br_federative_units.csv")

# Create a vector of geographic codes used in gtrends, including all states and "BR" for the whole country
federative_units <- c(paste0("BR-", sort(FUs$ABBREV)), "BR")

########################
# Create directory to store output files for the current date
# Use this to update GoogleTrends extraction folder for most recent date
# mainDir <- "./data/GoogleTrends/"
# day_date <- format(Sys.time(), format = "%Y_%m_%d")  # Get current date in YYYY_MM_DD format
# dir_path = paste0(mainDir, day_date)  # Combine base path with date
# dir.create(file.path(dir_path), showWarnings = FALSE)  # Create directory if it doesn't exist
dir_path = "./data/GoogleTrends/"

# Load and prepare search term queries

# Search terms for diseases
disease_query_tbl <- data.frame(
  group = c("Dengue", "Chikungunya", "Influenza", "COVID-19"),
  query = c("/m/09wsg", "/m/01__7l", "/m/0cycc", 
            'COVID-19%20%2B%20covid-19%20%2B%20covid%20%2B%20sarscov2%20%2B%20"covid%2019"%20%2B%20covid19')
)

# Load popular symptom-related search terms
popular_terms <- read.csv("data/google_trends/popular_terms.csv")

# Prepare query strings for coded topics (i.e., specific Google Trends topic codes using Freebase ID)
query_table_with_code <- popular_terms %>%
  filter(is_code) %>%
  group_by(group) %>%
  mutate(terms_code = paste0(terms, collapse = "%20%2B%20")) %>%  # Join all topic codes with "+"
  ungroup() %>%
  select(group, terms_code)

# Prepare query strings for non-coded (free text) search terms
query_table <- popular_terms %>% 
  filter(!is_code) %>%
  group_by(group) %>% 
  summarise(query = paste(trimws(str_to_lower(terms)), collapse = '\"%20%2B%20\"')) %>%  # Concatenate terms
  ungroup() %>%
  mutate(query = paste0('\"', query, '\"'),   # Wrap query in quotes
         query = gsub(" ", "%20", query)) %>%  # URL-encode spaces
  full_join(query_table_with_code, by = "group") %>%  # Merge with coded queries
  mutate(query = case_when(
    !is.na(terms_code) & is.na(query) ~ terms_code,
    !is.na(terms_code) & !is.na(query) ~ paste0(terms_code, "%20%2B%20", query),
    TRUE ~ query
  )) %>%
  bind_rows(disease_query_tbl) %>%  # Add disease queries to the full query list
  distinct()  # Remove duplicates

# Define time window for Google Trends queries

# Use current system date to define the most recent month and a 5-year window
most_recent_year_month <- format(Sys.time(), format = "%Y-%m")
five_years <- format(as.Date(Sys.time()) - 365*5 + 31, format = "%Y-%m")

# Alternatively, use a fixed reference date (useful for reproducibility)
set_fixed_date = as.Date("2024-12-31")
most_recent_year_month <- format(set_fixed_date, format = "%Y-%m")
five_years <- format(set_fixed_date - 365*5 + 31, format = "%Y-%m")

# Remove certain overlapping or redundant symptom groups from the query table
query_table <- query_table %>%
  filter(!group %in% c(
    "Perda de olfato", "Perda do paladar", "Alteração do paladar",
    "Alteração de olfato", "Dor ocular", "Dor atrás dos olhos"
  ))

########################
# Extract Google Trends data

ini = Sys.time()  # Record the start time of the extraction
gt_results <- data.frame()  # Initialize an empty dataframe to store successful results
error_df <- data.frame()    # Initialize an empty dataframe to store any errors

# Loop over each query (each symptom or disease)
for(n in 1:nrow(query_table)) {
  
  # Extract the group name (e.g., "Dengue") and its associated query string
  topic = unlist(query_table[n, "group"])
  topic_query = unlist(query_table[n, "query"])
  
  # Loop over each federative unit (e.g., BR-SP, BR-RJ, ..., BR)
  for (i in 1:28) {
    
    # Print status message with current topic, location, and time window
    print(paste0("Topic: ", topic,
                 "     Query for locations: ", federative_units[i],
                 "     Time-frame: ", five_years, " to ", most_recent_year_month))
    
    sys_time = substr(Sys.time(), 1, 16)  # Save the system time for recordkeeping
    error_message <- NA  # Reset error message variable before each API call
    
    # Try to retrieve Google Trends data via custom API wrapper
    gt_temp <- try_gtrends_api(
      topic_keyword = topic_query, 
      geo_location = federative_units[i], 
      start_date = five_years, 
      end_date = most_recent_year_month,
      api_key = your_api_key
    )
    
    # Stop the script if API rate limit (429) is reached
    if(error_message == "Status code was not 200. Returned status code:429" & !is.na(error_message)) {
      stop()
    }
    
    # If the result is NA (i.e., failed retrieval), store the error for later review
    if((length(gt_temp) == 1 & all(is.na(gt_temp)))) {
      
      error_df_temp <- data.frame(
        keyword = topic,
        geo = federative_units[i],
        time = paste0(five_years, " to ", most_recent_year_month),
        error = error_message
      )
      
      error_df <- rbind(error_df, error_df_temp)
      
    } else {
      # If data is successfully retrieved, clean and append to the results table
      gt_results <- bind_rows(gt_results,
                              gt_temp %>%
                                mutate(keyword2 = keyword) %>%
                                select(value, date, geo, time, keyword2) %>%
                                mutate(
                                  keyword = topic,               # Add original group name as keyword
                                  value = ifelse(value == "<1", 0.1, value),  # Convert "<1" to 0.1
                                  value = as.numeric(value),     # Ensure numeric type
                                  sys_time = sys_time            # Add system time for timestamping
                                )
      )
    }
  }
}

fin = Sys.time()  # Record end time
print(fin - ini)  # Print total processing time

# Clean and prepare GoogleTrends data
gt_ts_final <- gt_results %>%
  select(-all_of(c("sys_time", "time"))) %>%
  mutate(
    geo = substr(geo, 4, 5),
    geo = as.character(geo),
    geo = ifelse(geo == "", "BR", geo),
    location = factor(geo),
    date = as.Date(date),
    topic = factor(keyword)
  ) %>%
  select(date, location, topic, value) %>%
  complete(date, location, topic, fill = list(value = 0))

# Save successful results to CSV file
filename <- paste0(dir_path, "GoogleTrends_search.csv")
fwrite(gt_ts_final, filename)

# If any errors occurred, save the error log as well
if(nrow(error_df) > 0) {
  filename_error <- paste0(dir_path, "query_error.csv")  
  fwrite(error_df, filename_error)
}
