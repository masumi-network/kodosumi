Kodosumi was built to address the challenges in developing and operating agentic services. Agentic services are autonomous systems that solve real-world problems. Agents run in distributed environments and require effective coordination, monitoring, and interaction.

Kodosumi provides a comprehensive solution to these challenges through:

1. **Real-time Monitoring and Interaction**
   - Live monitoring of running services
   - Direct interaction with active services
   - Real-time logging of `stdio` (`stdout`, `stderr`), and debug messages

2. **Event-driven Architecture**
   - Coordination of multiple services through a unified event system
   - Tracking of service states (starting, running, finished, error)
   - Structured capture of metadata and results

3. **Unified API**
   - Easy discovery of available services
   - Standardized service execution
   - Access to execution results and event streams

These capabilities allow developers to focus on their agentic service logic while Kodosumi provides the infrastructure for scaling, monitoring, and interaction.

*Note: Kodosumi is built on [ray](https://ray.io) for distributed computing and [fastapi](https://fastapi.tiangolo.com/) for user interfaces. For setup details, see [kodosumi + ray startup](../README.md).*