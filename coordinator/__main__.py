try:
    from coordinator.coordinator import _max_concurrent, main
except ModuleNotFoundError:
    from coordinator import _max_concurrent, main


if __name__ == "__main__":
    main()
