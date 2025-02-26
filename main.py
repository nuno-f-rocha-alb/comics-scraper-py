import time

from comic_search.read_series_list import read_series_list
from comic_search.search_comics import search_comics
from config import *
from downloader.check_and_download_comics import check_and_download_comics
from util import create_series_directory


#test comment

def main():
    start_time = time.time()
    series_list = read_series_list(SERIES_FILE_PATH)

    #entry[0] -> publisher
    #entry[1] -> series_name
    #entry[2] -> year
    #entry[3] -> comicvine_volume_id

    for entry in series_list:
        logging.info(f"Searching for comics in series: {entry[1]} by {entry[0]}")
        available_comics = search_comics(entry)
        if available_comics:
            local_dir = create_series_directory(entry)
            check_and_download_comics(entry, available_comics, local_dir)
        else:
            logging.warning(f"No comics found for series: {entry[1]}")

    

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