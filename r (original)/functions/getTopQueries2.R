#' @title Get Top Search Queries from Google Trends
#' @description This function retrieves the top search queries related to a given search term
#' using the Google Trends v1beta API. You can specify geographic, temporal, and topical filters.
#'
#' @param terms A single character string indicating the search term or topic ID. Must be a single term.
#' @param geo Optional. A character string specifying the region to filter the results by, using ISO-3166-2 codes (e.g., "BR", "BR-SP").
#' Use \code{gtrendsAPI::regions} to see available region codes.
#' @param property Optional. A character string indicating the type of search to filter by.
#' Must be one of \code{"web"}, \code{"images"}, \code{"news"}, \code{"froogle"}, or \code{"youtube"}.
#' Defaults to \code{"web"}.
#' @param category Optional. A character string representing the category ID as used by Google Trends.
#' Use \code{gtrendsAPI::categories} to view available categories.
#' @param startDate Optional. A character string representing the start date in \code{"YYYY-MM"} format.
#' @param endDate Optional. A character string representing the end date in \code{"YYYY-MM"} format.
#' @param api.key A valid Google Trends API key (as a character string).
#'
#' @return A data frame with the following columns:
#' \itemize{
#'   \item \code{topSearches}: The top related search queries.
#'   \item \code{value}: Relative popularity score for each search term.
#'   \item \code{geo}: The geographical scope of the query.
#'   \item \code{time}: Time coverage of the query (start and end).
#'   \item \code{keyword}: The original search term.
#'   \item \code{gprop}: Type of search property (web, news, etc.).
#'   \item \code{category}: The category filter used.
#' }
#' If no results are found, a data frame with NA values is returned.
#'
#' @details
#' The function constructs a URL to query the Google Trends v1beta API, including optional filters for
#' geography, date range, category, and property. It returns the top search queries for the specified term.
#' It performs basic validation for input parameters and handles HTTP errors and empty responses.

getTopQueries2 <- function(terms, geo=NULL, property=NULL, category=NULL, startDate=NULL, endDate=NULL, api.key) {
  
  #Base link of API call
  link <- "https://www.googleapis.com/trends/v1beta/topQueries?"
  
  #Add serach terms to link
  #Search terms should be a vector of up to five strings
  if (is.character(terms) == F){
    stop("terms must be in string format", call. = FALSE)
  }
  
  if (length(terms) > 1){
    stop("terms should be a single string", call. = FALSE)
  }else{
    link <- paste0(link, "term=", terms)
  }
  
  #Add Google API key to link
  if (is.character(api.key) == F){
    stop("api.key must be in string format", call. = FALSE)
  }
  
  link <- paste0(link, "&key=", api.key)
  
  #Add Geo filter to link
  if (is.null(geo) == F){
    if(is.character(geo) == F){
      stop("geo must be in string format", call. = FALSE)
    }else{
      if(geo %in% gtrendsAPI::regions[,1] == F){
        stop("geo must an ISO-3166-2 region code - call regions for currently supported codes", call. = FALSE)
      }else{
        link <- paste0(link, "&restrictions.geo=", geo)
      }
    }
  }
  
  #Add Property filter
  if (is.null(property) == F){
    if(is.character(property) == F){
      stop("property must be in string format", call. = FALSE)
    }else{
      if(property %in% gtrendsAPI::properties == F){
        stop("property must be one of \"web\", \"images\", \"news\", \"froogle\" or \"youtube\". Defaults to \"web\".", call. = FALSE)
      }else{
        if(property != "web"){
          link <- paste0(link, "&restrictions.property=", property)
        }
      }
    }
  }
  
  #Add category filter
  if (is.null(category) == F){
    if(is.character(category) == F){
      stop("property must be in string format", call. = FALSE)
    }else{
      if(category %in% gtrendsAPI::categories[,1] == F){
        stop("property must be one of the categories recognized by Google", call. = FALSE)
      }else{
        categ <- as.numeric(unique(gtrendsAPI::categories[which(gtrendsAPI::categories==category),2]))
        link <- paste0(link, "&restrictions.category=", categ)
      }
    }
  }
  
  #Add startDate filter
  if (is.null(startDate) == F){
    if(is.character(startDate) == F){
      stop("startDate must be in string format", call. = FALSE)
    }else{
      if(grepl("^\\d{4}-\\d{2}$", startDate, perl=T) == F){
        stop("startDate should be a month and a year in the format YYYY-MM e.g. 2010-01", call. = FALSE)
      }else{
        link <- paste0(link, "&restrictions.startDate=", startDate)
      }
    }
  }
  
  #Add endDate filter
  if (is.null(endDate) == F){
    if(is.character(endDate) == F){
      stop("endDate must be in string format", call. = FALSE)
    }else{
      if(grepl("^\\d{4}-\\d{2}$",endDate, perl=T) == F){
        stop("endDate should be a month and a year in the format YYYY-MM e.g. 2010-01", call. = FALSE)
      }else{
        link <- paste0(link, "&restrictions.endDate=", endDate)
      }
    }
  }
  
  link <- paste0(link, "&hl=pt")
  
  #Encode url
  link <- utils::URLencode(link)
  
  #GET call to extract data
  f <- httr::GET(link)
  
  if(httr::http_error(f) == T){
    
    stop(paste0(jsonlite::fromJSON(httr::content(f, as = "text"))$error$code, " - ", jsonlite::fromJSON(httr::content(f, as = "text"))$error$message), call. = FALSE)
    
  }else{
    
    #Convert data to JSON format
    d <- jsonlite::fromJSON(httr::content(f, as = "text"))
    
    #Arrange data table
    res <- d$item
    
    if(length(d)==0){
      
      res<-data.frame(a=NA, b=NA)
      names(res)<-c("topSearches", "value")
      
    }else{
      
      #Change column names to match standard output
      names(res)<-c("topSearches", "value")
      
    }
    
    #Add geographical scope of search
    if(is.null(geo) == T){
      res$geo<-"world"
    }else{
      res$geo<-geo
    }
    
    #Add temporal coverage
    if(is.null(startDate) == T & is.null(endDate) == T){
      res$time<-paste0("2004-01 ", format(Sys.Date(), "%Y-%m"))
    }
    
    if(is.null(startDate) == T & is.null(endDate) == F){
      res$time<-paste0("2004-01 ", endDate)
    }
    
    if(is.null(startDate) == F & is.null(endDate) == T){
      res$time<-paste0(startDate, " ", format(Sys.Date(), "%Y-%m"))
    }
    
    if(is.null(startDate) == F & is.null(endDate) == F){
      res$time<-paste0(startDate, " ", endDate)
    }
    
    #Add keyword
    res$keyword<-terms
    
    #Add property searched
    if(is.null(property) == T){
      res$gprop<-"web"
    }else{
      res$gprop<-property
    }
    
    #Add category filter
    if(is.null(category) == T){
      res$category<-"All categories"
    }else{
      res$category<-category
    }
    
  }
  
  return(res)
  
}

