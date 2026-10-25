"""Model inventory CLI; implementation is shared with managed update preflight."""
from guard.adapters.model_inventory import inventory, vllm_read_files, main

if __name__ == "__main__":
    main()
