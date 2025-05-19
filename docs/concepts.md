# kodosumi concepts

This document provides an overview of the key concepts that constitute the Kodosumi framework. 

## Core Concepts

### Flow

A _Flow_ represents an automated process, workflow, or system of interconnected tasks working towards a common objective. While similar concepts exist in different contexts (such as _[agents](#agents)_, _workflows_, _objects_, _methods_, _functions_, _models_, or _[agentic services](#agentic-service)_), we use the term _Flow_ to emphasize its process-oriented nature. 

### Agentic Service

An Agentic Service is a self-contained, deployable unit within the Kodosumi framework. It integrates one or more [Flows](#flows) with required resources and configurations to deliver complete functionality. An agentic service comprises [endpoints](#endpoint) and [entrypoints](#entrypoint) into [flows](#flows). Service [endpoints](#endpoint) implement request/response patterns, while [flows](#flows) implement business logic and return intermediate and final results among other events. The [entrypoint](#entrypoint) is a Python object or callable to enter flow execution.

### Agent

An Agent is an autonomous object within the Kodosumi framework that can perform specific tasks or services. Agents can interact with other agents, process data, and execute complex workflows. Since the term _Agent_ is overloaded, so we prefer to use _Flow_ to represent automation and workflows.

### Panel

The Panel serves as Kodosumi's administrative interface. It enables [flow execution](#flow-execution) management in the Ray cluster, including launching, monitoring, and reviewing flow events and results.

## Ray Concepts

### Ray Head

The Ray Head is the central coordinator of the Ray cluster. It manages cluster resources, handles task scheduling, and maintains the cluster's state. The Ray Head is responsible for coordinating communication between Ray workers and managing the overall cluster health.

**See also:**
- [Get Started with Ray](https://docs.ray.io/en/latest/ray-overview/getting-started.html)
- [Ray Key Concepts](https://docs.ray.io/en/latest/ray-core/key-concepts.html)
- [Ray Serve](https://docs.ray.io/en/latest/serve/getting_started.html)

### Ray Worker

Ray Workers are the execution nodes in the Ray cluster. They perform the actual computation of tasks assigned by the Ray Head. Workers can be dynamically scaled up or down based on workload demands.

**See also:**
- [Ray Actors, Workers and Resources](https://docs.ray.io/en/latest/ray-core/actors.html#faq-actors-workers-and-resources)

### Ray Cluster

The Ray Cluster is the distributed computing backbone of Kodosumi. It provides the infrastructure for parallel and distributed execution of tasks, enabling scalable and fault-tolerant service deployment.

### Ray Driver

The Ray Driver is the process that runs the main program in a Ray cluster. It is the entry point for Ray applications and is responsible for submitting tasks to the cluster. The Driver connects to the Ray cluster and coordinates the execution of distributed computations.

**See also:**
- [Ray Driver](https://docs.ray.io/en/latest/ray-core/key-concepts.html#ray-driver)

## Flow Execution

### Flow Register

Flow Registers are the source locations where Flow [endpoints](#endpoint) are registered in the system. They maintain a catalog of available Flow sources, their endpoints, and their [entrypoints](#entrypoint), enabling the discovery and routing of Flow operations.

### Endpoint

Endpoints are the defined interfaces of an Agentic Service that expose its functionality externally. They implement the Request/Response pattern and enable service interaction through standardized protocols.

### Entrypoint

Entrypoints are the internal entry locations within an Agentic Service where Flows can be initiated or connected. They serve as the bridge between [Endpoints](#endpoint) and [Flow implementations](#flows), defining how external requests are transformed into Flow executions.

### Serve API

Serve API is Kodosumi's specialized wrapper around FastAPI, providing enhanced functionality for building HTTP [endpoints](#endpoint) for [agentic services](#agentic-service). It integrates seamlessly with Ray Serve and offers standardized patterns for API development.

### Runner

The Runner is a detached Ray actor created by the Panel to manage service execution. Once launched, it operates autonomously within the [Ray cluster](#ray-cluster), handling the complete service lifecycle. The Runner interacts with the [Spooler](#spooler) to manage and persist the [Event Stream](#event-stream).

### Spooler

The Spooler is a specialized component in Kodosumi that handles [event stream](#event-stream) persistence. It processes events from [Flows](#flows) and ensures their reliable storage in the [event stream](#event-stream). The Spooler maintains the event history and makes it accessible throughout the Kodosumi API.

### Event Stream

The Event Stream is a real-time communication channel that enables asynchronous event-driven interactions between different components of the Kodosumi system.
