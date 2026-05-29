#' Safely retrieve Google Trends data using a custom API wrapper
#'
#' This function wraps API calls to custom Google Trends functions (`getGraph2`, `getTopTopics2`, or `getTopQueries2`) inside a `tryCatch()` block. 
#' It returns `NA` if an error occurs, storing the error message in a global variable `error_message`.
#'
#' @param topic_keyword Character. The search term or topic keyword to retrieve Google Trends data for.
#' @param geo_location Character. The location for which to retrieve data (e.g., "BR" for Brazil, "US" for United States, or "" for worldwide).
#' @param start_date Character or `Date`. Optional. The start date for the data range in "YYYY-MM-DD" format.
#' @param end_date Character or `Date`. Optional. The end date for the data range in "YYYY-MM-DD" format.
#' @param fun Character. Type of information to retrieve. Options are `"graph"` (default), `"topics"`, or `"queries"`.
#'
#' @return A list containing the requested Google Trends data if successful, or `NA` if an error occurs.
#'
#' @details This function uses a hardcoded API key to access Google Trends data via custom wrapper functions. 
#' The argument `fun` determines the type of data retrieved:
#' \itemize{
#'   \item `"graph"`: time series trend data (`getGraph2`)
#'   \item `"topics"`: top associated topics (`getTopTopics2`)
#'   \item `"queries"`: top related search queries (`getTopQueries2`)
#' }
#'
#' If the API call fails (due to an invalid keyword, region, time range, or network/API error), the function stores the error message in a 
#' global variable `error_message` and returns `NA`.

try_gtrends_api <- function(topic_keyword, geo_location, start_date = NULL, end_date = NULL, fun = "graph", api_key) {
  
  tryCatch({
    # Code that might produce an error
    
    
    if(fun == "graph") {
    output_gtrends = getGraph2(terms = topic_keyword,
                               geo = geo_location,
                               startDate = start_date,
                               endDate = end_date,
                               api.key = your_api_key)   
    }
    
    if(fun == "topics") {
      output_gtrends = getTopTopics2(terms = topic_keyword,
                                 geo = geo_location,
                                 startDate = start_date,
                                 endDate = end_date,
                                 api.key = your_api_key)   
    }
    
    if(fun == "queries") {
      output_gtrends = getTopQueries2(terms = topic_keyword,
                                 geo = geo_location,
                                 startDate = start_date,
                                 endDate = end_date,
                                 api.key = your_api_key)   
    }
    
    
    
    return(output_gtrends)
  }, error = function(e) {
    # If an error occurs, save the error message
    error_message <<- e$message
    return(NA)  # Return NA or any other value as a fallback
  })
  
}


