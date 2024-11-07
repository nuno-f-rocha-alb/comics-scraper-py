import time

from comic_search.read_series_list import read_series_list
from comic_search.search_comics import search_comics
from config import *
from downloader.check_and_download_comics import check_and_download_comics
from util import create_series_directory


def main():
    start_time = time.time()
    series_list = read_series_list(SERIES_FILE_PATH)

    for publisher, series_name, year in series_list:
        logging.info(f"Searching for comics in series: {series_name} by {publisher}")
        available_comics = search_comics(series_name, year)
        if available_comics:
            local_dir = create_series_directory(publisher, series_name, year)
            check_and_download_comics(series_name, available_comics, local_dir, year)
        else:
            logging.warning(f"No comics found for series: {series_name}")

    end_time = time.time()
    elapsed_time = end_time - start_time

    # Convert elapsed time to minutes and seconds
    minutes = int(elapsed_time // 60)
    seconds = elapsed_time % 60

    # Log the elapsed time in minutes and seconds if it's more than 60 seconds
    if minutes > 0:
        logging.info(f"Total execution time: {minutes} minutes and {seconds:.2f} seconds")
    else:
        logging.info(f"Total execution time: {seconds:.2f} seconds")


if __name__ == "__main__":
    main()