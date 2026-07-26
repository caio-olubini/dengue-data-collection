# Load required packages
require(tidyverse)     # For data manipulation and visualization
require(gtrendsAPI)    # To access Google Trends API
require(data.table)    # For fast data manipulation and file writing

# Load custom functions
source("r/functions/fun.R")
source("r/functions/getGraph2.R")
source("r/functions/getTopTopics2.R")
source("r/functions/getTopQueries2.R")

########################################################################
# IMPORTANT
# Define here your api_key

your_api_key <- "..."

########################################################################

# Load state codes (federative units) in Brazil
FUs <- read.csv("data/epidemiological/br_federative_units.csv")
# Create a vector of Google Trends codes for each state, plus the whole country ("BR")
FU_code <- c(paste0("BR-",sort(FUs$ABBREVIATION)), "BR")

########################
# Create directory to store output files
dir_path <- "./data/GT/top_results"
dir.create(file.path(dir_path), showWarnings = FALSE)

# Define search terms and prepare queries

# Table of disease-related search terms and corresponding topic codes or queries
disease_query_tbl <- data.frame(
  group = c("Dengue", "Chikungunya", "Influenza/gripe", "COVID-19"),
  query = c("/m/09wsg", "/m/01__7l", "/m/0cycc",
            'COVID-19%20%2B%20covid-19%20%2B%20covid%20%2B%20sarscov2%20%2B%20"covid%2019"%20%2B%20covid19')
)

# Define time range (monthly) from Jan 2020 to 30 days before current date
most_recent_year_month <- format(Sys.Date() - 30, format = "%Y-%m")
time_seq <- as.character(seq(as.Date("2020-01-01"), as.Date(paste0(most_recent_year_month, "-01")), by = "month"))

########################
# Extract data from Google Trends

# Track processing time
ini = Sys.time()

# Initialize result and error data.frames
gt_results_topic <- data.frame()
error_df_topic <- data.frame()
gt_results_query <- data.frame()
error_df_query <- data.frame()

# Loop through diseases (4 rows in the query table)
for(n in 1:4) {
  
  topic = unlist(disease_query_tbl[n, "group"])
  topic_query = unlist(disease_query_tbl[n, "query"])
  
  # Loop through 27 Brazilian states + BR (total 28 geolocations)
  for (i in 1:28) {
    
    # Loop through each month in time range
    for(t in time_seq) {
      
      month_date <- substr(as.character(t), 1, 7)  # Get year-month format
      
      print(paste0("Topic: ", topic,
                   "     Query for locations: ", FU_code[i],
                   "     Time-frame: ", month_date))
      
      sys_time = substr(Sys.time(), 1, 16)  # Record system time for tracking
      
      ## -------- Get TOPICS -------- ##
      error_message <- NA  # Reset error message
      
      # Try to get Google Trends data for related topics
      gt_temp_topic <- try_gtrends_api(
        topic_keyword = topic_query,
        geo_location = FU_code[i],
        start_date = month_date,
        end_date = month_date,
        fun = "topics",
        api_key = your_api_key
      )
      
      # Stop execution if API rate limit is exceeded
      if(error_message == "Status code was not 200. Returned status code:429" &
         !is.na(error_message)) {stop()}
      
      # Save error if no data was returned
      if((length(gt_temp_topic) == 1 & all(is.na(gt_temp_topic)))) {
        error_df_temp_topic <- data.frame(
          keyword = topic,
          geo = FU_code[i],
          time = month_date,
          error = error_message
        )
        error_df_topic <- rbind(error_df_topic, error_df_temp_topic)
        
      } else {
        # Append valid results to main data frame
        gt_results_topic <- bind_rows(gt_results_topic,
                                      gt_temp_topic %>%
                                        select(topicTitle, topicId, value, geo) %>%
                                        mutate(
                                          keyword = topic,
                                          time = month_date,
                                          value = ifelse(value == "<1", 0.1, value),  # Replace "<1" with 0.1
                                          value = as.numeric(value),
                                          sys_time = sys_time
                                        )
        )
      }
      
      ## -------- Get QUERIES -------- ##
      error_message <- NA  # Reset error message again
      
      # Try to get Google Trends data for top related search queries
      gt_temp_query <- try_gtrends_api(
        topic_keyword = topic_query,
        geo_location = FU_code[i],
        start_date = month_date,
        end_date = month_date,
        fun = "queries",
        api_key = your_api_key
      )
      
      # Save error if no data returned
      if((length(gt_temp_query) == 1 & all(is.na(gt_temp_query)))) {
        error_df_temp_query <- data.frame(
          keyword = topic,
          geo = FU_code[i],
          time = month_date,
          error = error_message
        )
        error_df_query <- rbind(error_df_query, error_df_temp_query)
        
      } else {
        # Append valid query results to main data frame
        gt_results_query <- bind_rows(gt_results_query,
                                      gt_temp_query %>%
                                        select(topSearches, value, geo) %>%
                                        mutate(
                                          keyword = topic,
                                          time = month_date,
                                          value = ifelse(value == "<1", 0.1, value),
                                          value = as.numeric(value),
                                          sys_time = sys_time
                                        )
        )
      }
    }
  }
}

# Print total time spent
fin = Sys.time()
print(fin - ini)

# Save topic results to CSV

# Load symptom codes
popular_terms <- read.csv("data/google_trends/popular_terms.csv")

symptom_codes = popular_terms %>%
  filter(!exclude) %>%
  mutate(is_code = grepl("/", terms)) %>%
  filter(is_code) %>% select(terms, is_code) |>
  distinct()

gt_s <- gt_results_topic |>
  select(topicTitle, topicId, time, geo, value, keyword) |>
  left_join(symptom_codes, by = c("topicId" = "terms")) %>%
  mutate(is_code = ifelse(is.na(is_code), F, T),
         is_code = ifelse(topicTitle == 'Symptom', TRUE, is_code),
         geo = ifelse(nchar(geo) == 5, substr(geo, 4,5), geo))

colnames(gt_s) <- c("topic_title", "topic_id", "date", "location", "value", "disease", "key_symptom")

filename <- paste0(dir_path, "/GoogleTrends_related_topic.csv")
fwrite(gt_s, filename)

# Save topic errors (if any)
if(nrow(error_df_topic) > 0) {
  filename_error <- paste0(dir_path, "/query_error_topic.csv")
  fwrite(error_df_topic, filename_error)
}

# Save query results to CSV
gt_q <- gt_results_query |>
  select(topSearches, time, geo, value, keyword) |>
  mutate(geo = ifelse(nchar(geo) == 5, substr(geo, 4,5), geo))

colnames(gt_q) <- c("topic_title", "date", "location", "value", "disease")

# Save query results to CSV
filename <- paste0(dir_path, "/GoogleTrends_related_query.csv")
fwrite(gt_q, filename)

# Save query errors (if any)
if(nrow(error_df_query) > 0) {
  filename_error <- paste0(dir_path, "/query_error_query.csv")
  fwrite(error_df_query, filename_error)
}

