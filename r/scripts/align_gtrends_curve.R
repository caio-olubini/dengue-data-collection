# Load required packages
# Install 'gtrendsAPI' from GitHub if not already installed
if (!requireNamespace("gtrendsAPI", quietly = TRUE)) 
  devtools::install_github("racorreia/gtrendsAPI", build_vignettes = TRUE)

# Load necessary packages into the session
require(gtrendsAPI)  # Google Trends API wrapper
require(tidyverse)   # Data wrangling and visualization
require(data.table)  # Efficient data manipulation and I/O

# Load custom functions from local R scripts
source("r/functions/fun.R")
source("r/functions/getGraph2.R")

########################################################################
# IMPORTANT: Define your Google API key here
your_api_key <- "..."
########################################################################

# Retrieve Google Trends data for "Dengue" in Brazil between June 2016 and June 2020
gt_1 <- try_gtrends_api(
  topic_keyword = "/m/09wsg",  # Freebase ID for Dengue
  geo_location = "BR",         # Brazil
  start_date = "2016-06",
  end_date = "2020-06",
  api_key = your_api_key
)

# Retrieve Google Trends data for the same topic between June 2019 and June 2024
gt_2 <- try_gtrends_api(
  topic_keyword = "/m/09wsg",
  geo_location = "BR",
  start_date = "2019-06",
  end_date = "2024-06",
  api_key = your_api_key
)

# Combine the two datasets to compare trends across overlapping periods
gt_results_tbl_joined <- gt_1 %>%
  select(value, date) %>%                              # Select relevant columns
  full_join(gt_2 %>% select(value, date), by = "date") %>%  # Merge by date (full join)
  pivot_longer(names_to = "type", values_to = "value", -date) %>%  # Reshape to long format
  mutate(date = as.Date(date)) %>%                     # Ensure date is in Date format
  arrange(date)                                        # Sort chronologically

# Plot the comparison of search interest from both extractions
p1 <- gt_results_tbl_joined %>% 
  ggplot(aes(x = date, y = value, color = type)) +
  geom_line(size = 1) +
  theme_minimal() +
  scale_color_discrete("Google Trends extraction",
                       label = c("Extraction 1", "Extraction 2")) +
  ylab("Search Interest Index") +
  xlab("Date") +
  scale_y_continuous(expand = c(0, 0)) +
  theme(
    legend.position = "bottom",
    axis.line = element_line(size = 0.5, colour = "black"),
    plot.background = element_rect(fill = "white"),
    text = element_text(size = 16)
  )

p1  # Display the first comparison plot

# Calculate adjustment factor to align the scale between the two series
gt_results_tbl_ratio <- gt_results_tbl_joined %>% 
  pivot_wider(names_from = type, values_from = value, id_cols = date) %>%  # Wide format
  drop_na() %>%                                                            # Remove rows with NA
  mutate(
    ratio = value.x / value.y,                                             # Ratio between series
    mean.ratio = mean(ratio),                                             # Mean ratio (rescaling factor)
    new_x = value.x / mean.ratio                                          # Rescaled values
  )

adjust_factor <- gt_results_tbl_ratio$mean.ratio[1]  # Store rescaling factor

# Plot both series again, with Extraction 1 rescaled to match Extraction 2
p2 <- gt_results_tbl_joined %>% 
  pivot_wider(names_from = type, values_from = value, id_cols = date) %>%
  mutate(new_x = value.x / adjust_factor) %>%                             # Rescale Extraction 1
  pivot_longer(names_to = "type", values_to = "value", -date) %>%
  mutate(value = value / max(value, na.rm = TRUE) * 100) %>%              # Normalize to 0–100
  filter(type != "value.x") %>%                                           # Drop unrescaled series
  ggplot(aes(x = date, y = value, color = type)) +
  geom_line() +
  theme_minimal() +
  scale_color_discrete("Google Trends extraction",
                       label = c("Extraction 1", "Extraction 2 resized")) +
  ylab("Search Interest Index") +
  xlab("Date") +
  scale_y_continuous(expand = c(0, 0)) +
  theme(
    legend.position = "bottom",
    axis.line = element_line(size = 0.5, colour = "black"),
    plot.background = element_rect(fill = "white"),
    text = element_text(size = 16)
  )

p2  # Display the second plot (rescaled comparison)

# Prepare the final adjusted series, selecting the best available value for each date
gt_results_tbl_final <- gt_results_tbl_joined %>% 
  pivot_wider(names_from = type, values_from = value, id_cols = date) %>%
  mutate(new_x = value.x / adjust_factor) %>%                            # Rescale older extraction
  pivot_longer(names_to = "type", values_to = "value", -date) %>%
  mutate(value = value / max(value, na.rm = TRUE) * 100) %>%             # Normalize to 0–100
  pivot_wider(names_from = type, values_from = value, id_cols = date) %>%
  mutate(
    final_value = round(ifelse(is.na(value.y), new_x, value.y), 1)       # Choose best available value
  ) %>%
  select(date, final_value)                                              # Final output: date + adjusted value

gt_results_tbl_final  # View final harmonized time series

